"""
QUANTITATIVE AUDIT: REAL-WORLD LIVE MARKET PYRAMIDING SIMULATOR
================================================================
Evaluating 5 Pyramiding Variants on XAUUSD (Gold) under full Live Market Frictions:
- Asset: XAUUSD (Gold)
- Live Vantage Spread: $0.30 per oz
- Commission: $6.00 per lot ($0.06/oz)
- Latency Slippage: $0.15 entry, $0.08 exit (345ms ping simulation)
- Mandatory Auto Square-Off: 15:55 IST (100% Flat daily)
- Maximum Allowed Risk per Trade: 2.0% ($200 on $10,000 initial balance)
- Daily Circuit Breaker: -2.00% max daily loss

Pyramiding Variants Evaluated:
1. Benchmark: Full 2.0% Single Entry (No Pyramiding)
2. Variant 1: Aggressive Equal-Weight Pyramiding (1.0% Initial + 1.0% Pyramid at +1.0R, SL1 to BE)
3. Variant 2: Front-Loaded Pyramiding (1.33% Initial + 0.67% Pyramid at +1.0R, SL1 to BE)
4. Variant 3: Guaranteed Free-Roll (1.0% Initial + 0.5% Pyramid at +1.0R, SL1 to +0.5R Lock)
5. Variant 4: Early Scale-In (1.0% Initial + 1.0% Pyramid at +0.75R, SL1 to BE)
6. Variant 5: Deep Trend Runner (1.0% Initial + 1.0% Pyramid at +1.25R, SL1 to +0.5R Lock)
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import datetime
import pandas as pd
import numpy as np
import MetaTrader5 as mt5

def load_gold_sessions():
    if not mt5.initialize():
        raise RuntimeError("MT5 init failed")
    
    dt_from = datetime.datetime(2022, 1, 1)
    dt_to = datetime.datetime(2026, 11, 30)
    rates = mt5.copy_rates_range("XAUUSD", mt5.TIMEFRAME_M5, dt_from, dt_to)
    mt5.shutdown()
    
    df = pd.DataFrame(rates)
    df['broker_dt'] = pd.to_datetime(df['time'], unit='s')
    df['ist_dt'] = df['broker_dt'] + datetime.timedelta(hours=3, minutes=0)
    df['date'] = df['ist_dt'].dt.date
    df['ist_hour'] = df['ist_dt'].dt.hour
    df['ist_minute'] = df['ist_dt'].dt.minute
    df['ema200'] = df['close'].ewm(span=200, adjust=False).mean()
    
    # Pre-group sessions between 11:00 and 16:00 IST
    df_day = df[(df['ist_hour'] >= 11) & (df['ist_hour'] < 16)].copy().reset_index(drop=True)
    sessions = {}
    for d, sub in df_day.groupby('date'):
        if len(sub) >= 40:
            sessions[d] = sub.to_dict('records')
    return sessions

def run_pyramiding_simulation(
    sessions,
    variant_name="Equal-Weight 1.0% + 1.0%",
    r1_pct=0.010,       # Initial risk %
    r2_pct=0.010,       # Pyramid risk %
    pyr_trigger_r=1.0,  # Move to add Tranche 2 (e.g. +1.0R)
    lock_sl1_r=0.0,     # Stop adjustment for Tranche 1 (0.0 = Breakeven, 0.5 = lock +0.5R)
    sl_dollars=6.00,    # Base Stop Loss distance ($)
    rr_multiplier=2.0,  # Overall TP distance = sl_dollars * rr_multiplier ($12.00)
    use_limit_entry=True
):
    spread = 0.30
    comm_per_lot = 6.00
    slip_in = 0.00 if use_limit_entry else 0.15 # 0 slippage for maker limit orders
    slip_out = 0.08
    contract_sz = 100.0 # 100 oz per standard lot
    
    tp_dollars = sl_dollars * rr_multiplier
    break_threshold = 3.50
    limit_offset = 1.50
    
    balance = 10000.0
    daily_records = []
    trade_records = []
    total_toll_paid = 0.0
    
    all_dates = sorted(list(sessions.keys()))
    
    for curr_d in all_dates:
        bars = sessions[curr_d]
        day_pnl = 0.0
        day_stopped = False
        
        # State tracking for the trading day
        phase = 'WAIT_ANCHOR'
        base_p = None
        ema_val = None
        direction = None
        limit_entry_p = None
        
        # Positions
        tranche1 = None
        tranche2 = None
        pyramided = False
        
        for i, b in enumerate(bars):
            h = b['ist_hour']
            m = b['ist_minute']
            
            # 1. HARD AUTO-SQUARE-OFF AT 15:55 IST
            if h == 15 and m >= 55:
                if tranche1 is not None:
                    # Close Tranche 1
                    exit_p = (b['close'] - (spread/2.0) - slip_out) if tranche1['type'] == 'BUY' else (b['close'] + (spread/2.0) + slip_out)
                    net_pts = (exit_p - tranche1['fill_p']) if tranche1['type'] == 'BUY' else (tranche1['fill_p'] - exit_p)
                    t1_pnl = (net_pts * tranche1['lots'] * contract_sz) - tranche1['comm']
                    day_pnl += t1_pnl
                    total_toll_paid += tranche1['comm']
                    trade_records.append({'pnl': t1_pnl, 'r': t1_pnl / (balance * r1_pct) if r1_pct > 0 else 0})
                    tranche1 = None
                    
                if tranche2 is not None:
                    # Close Tranche 2
                    exit_p = (b['close'] - (spread/2.0) - slip_out) if tranche2['type'] == 'BUY' else (b['close'] + (spread/2.0) + slip_out)
                    net_pts = (exit_p - tranche2['fill_p']) if tranche2['type'] == 'BUY' else (tranche2['fill_p'] - exit_p)
                    t2_pnl = (net_pts * tranche2['lots'] * contract_sz) - tranche2['comm']
                    day_pnl += t2_pnl
                    total_toll_paid += tranche2['comm']
                    trade_records.append({'pnl': t2_pnl, 'r': t2_pnl / (balance * r2_pct) if r2_pct > 0 else 0})
                    tranche2 = None
                break
                
            # 2. Check Daily Circuit Breaker (-2.00%)
            if day_pnl <= -(balance * 0.020):
                day_stopped = True
                if tranche1 is not None or tranche2 is not None:
                    # Emergency liquidation
                    for t in [tranche1, tranche2]:
                        if t is not None:
                            exit_p = (b['close'] - (spread/2.0) - slip_out) if t['type'] == 'BUY' else (b['close'] + (spread/2.0) + slip_out)
                            net_pts = (exit_p - t['fill_p']) if t['type'] == 'BUY' else (t['fill_p'] - exit_p)
                            pnl = (net_pts * t['lots'] * contract_sz) - t['comm']
                            day_pnl += pnl
                            total_toll_paid += t['comm']
                    tranche1 = None
                    tranche2 = None
                break
                
            # 3. Anchor at 12:00 PM IST (Frankfurt Pre-Open)
            if phase == 'WAIT_ANCHOR' and h == 12 and m == 0:
                base_p = b['close']
                ema_val = b['ema200']
                phase = 'WAIT_BREAK'
                continue
                
            # 4. Wait for breakout threshold ($3.50)
            if phase == 'WAIT_BREAK':
                if h >= 13: # Expiry
                    phase = 'DONE'
                    continue
                if base_p >= ema_val and b['high'] >= base_p + break_threshold:
                    phase = 'WAIT_LIMIT_FILL'
                    direction = 'BUY'
                    limit_entry_p = base_p + limit_offset
                elif base_p <= ema_val and b['low'] <= base_p - break_threshold:
                    phase = 'WAIT_LIMIT_FILL'
                    direction = 'SELL'
                    limit_entry_p = base_p - limit_offset
                    
            # 5. Wait for limit order fill on pullback
            elif phase == 'WAIT_LIMIT_FILL':
                if h >= 13: # Cancel if not filled before 13:00 IST
                    phase = 'DONE'
                    continue
                filled = False
                if direction == 'BUY' and b['low'] <= limit_entry_p:
                    fill_p = limit_entry_p + (spread / 2.0)
                    filled = True
                elif direction == 'SELL' and b['high'] >= limit_entry_p:
                    fill_p = limit_entry_p - (spread / 2.0)
                    filled = True
                    
                if filled:
                    phase = 'IN_TRADE'
                    # Position sizing Tranche 1
                    risk_cash_t1 = balance * r1_pct
                    raw_lots_t1 = risk_cash_t1 / (sl_dollars * contract_sz)
                    lots_t1 = max(0.01, round(raw_lots_t1, 2))
                    comm_t1 = (lots_t1 * comm_per_lot)
                    
                    tranche1 = {
                        'type': direction,
                        'fill_p': fill_p,
                        'sl': (fill_p - sl_dollars) if direction == 'BUY' else (fill_p + sl_dollars),
                        'tp': (fill_p + tp_dollars) if direction == 'BUY' else (fill_p - tp_dollars),
                        'lots': lots_t1,
                        'comm': comm_t1
                    }
                    total_toll_paid += comm_t1
                    
            # 6. Manage Active Positions & Check Pyramiding
            elif phase == 'IN_TRADE':
                ttype = tranche1['type'] if tranche1 is not None else (tranche2['type'] if tranche2 is not None else None)
                if ttype is None:
                    phase = 'DONE'
                    continue
                    
                # A. CHECK PYRAMID TRIGGER (if not yet added and Tranche 2 risk > 0)
                if not pyramided and r2_pct > 0 and tranche1 is not None:
                    trigger_dist = pyr_trigger_r * sl_dollars
                    can_pyramid = False
                    if ttype == 'BUY' and b['high'] >= tranche1['fill_p'] + trigger_dist:
                        can_pyramid = True
                        pyr_entry_raw = tranche1['fill_p'] + trigger_dist
                    elif ttype == 'SELL' and b['low'] <= tranche1['fill_p'] - trigger_dist:
                        can_pyramid = True
                        pyr_entry_raw = tranche1['fill_p'] - trigger_dist
                        
                    if can_pyramid:
                        pyramided = True
                        # Tranche 2 fills with taker spread + latency slippage
                        fill_p2 = (pyr_entry_raw + (spread/2.0) + 0.15) if ttype == 'BUY' else (pyr_entry_raw - (spread/2.0) - 0.15)
                        
                        # Move Tranche 1 Stop Loss to Breakeven or Locked Profit
                        if lock_sl1_r == 0.0:
                            # Breakeven
                            new_sl1 = tranche1['fill_p'] + (0.10 if ttype == 'BUY' else -0.10) # Cover spread
                        else:
                            # Lock in profit
                            new_sl1 = tranche1['fill_p'] + (lock_sl1_r * sl_dollars if ttype == 'BUY' else -(lock_sl1_r * sl_dollars))
                        tranche1['sl'] = new_sl1
                        
                        # Set Tranche 2 Sizing & Stop
                        # SL for Tranche 2 is set at Tranche 1's new stop level!
                        sl2_dist = abs(fill_p2 - new_sl1)
                        risk_cash_t2 = balance * r2_pct
                        raw_lots_t2 = risk_cash_t2 / (sl2_dist * contract_sz) if sl2_dist > 0 else lots_t1
                        lots_t2 = max(0.01, round(raw_lots_t2, 2))
                        comm_t2 = (lots_t2 * comm_per_lot)
                        total_toll_paid += comm_t2
                        
                        tranche2 = {
                            'type': ttype,
                            'fill_p': fill_p2,
                            'sl': new_sl1,
                            'tp': tranche1['tp'], # Same destination target
                            'lots': lots_t2,
                            'comm': comm_t2
                        }
                        
                # B. CHECK EXITS FOR TRANCHE 1 & TRANCHE 2
                # Check Take Profit
                if tranche1 is not None:
                    hit_tp = (b['high'] >= tranche1['tp']) if ttype == 'BUY' else (b['low'] <= tranche1['tp'])
                    hit_sl = (b['low'] <= tranche1['sl']) if ttype == 'BUY' else (b['high'] >= tranche1['sl'])
                    
                    if hit_tp:
                        exit_p = (tranche1['tp'] - (spread/2.0) - slip_out) if ttype == 'BUY' else (tranche1['tp'] + (spread/2.0) + slip_out)
                        net_pts = (exit_p - tranche1['fill_p']) if ttype == 'BUY' else (tranche1['fill_p'] - exit_p)
                        pnl1 = (net_pts * tranche1['lots'] * contract_sz) - tranche1['comm']
                        day_pnl += pnl1
                        trade_records.append({'pnl': pnl1, 'r': pnl1 / (balance * r1_pct)})
                        tranche1 = None
                        
                        if tranche2 is not None:
                            exit_p2 = (tranche2['tp'] - (spread/2.0) - slip_out) if ttype == 'BUY' else (tranche2['tp'] + (spread/2.0) + slip_out)
                            net_pts2 = (exit_p2 - tranche2['fill_p']) if ttype == 'BUY' else (tranche2['fill_p'] - exit_p2)
                            pnl2 = (net_pts2 * tranche2['lots'] * contract_sz) - tranche2['comm']
                            day_pnl += pnl2
                            trade_records.append({'pnl': pnl2, 'r': pnl2 / (balance * r2_pct)})
                            tranche2 = None
                        phase = 'DONE'
                        break
                        
                    elif hit_sl:
                        exit_p = (tranche1['sl'] - (spread/2.0) - slip_out) if ttype == 'BUY' else (tranche1['sl'] + (spread/2.0) + slip_out)
                        net_pts = (exit_p - tranche1['fill_p']) if ttype == 'BUY' else (tranche1['fill_p'] - exit_p)
                        pnl1 = (net_pts * tranche1['lots'] * contract_sz) - tranche1['comm']
                        day_pnl += pnl1
                        trade_records.append({'pnl': pnl1, 'r': pnl1 / (balance * r1_pct)})
                        tranche1 = None
                        
                        if tranche2 is not None:
                            exit_p2 = (tranche2['sl'] - (spread/2.0) - slip_out) if ttype == 'BUY' else (tranche2['sl'] + (spread/2.0) + slip_out)
                            net_pts2 = (exit_p2 - tranche2['fill_p']) if ttype == 'BUY' else (tranche2['fill_p'] - exit_p2)
                            pnl2 = (net_pts2 * tranche2['lots'] * contract_sz) - tranche2['comm']
                            day_pnl += pnl2
                            trade_records.append({'pnl': pnl2, 'r': pnl2 / (balance * r2_pct)})
                            tranche2 = None
                        phase = 'DONE'
                        break
                        
        # End of day settlement
        max_daily_loss = -(balance * 0.020)
        if day_pnl < max_daily_loss:
            day_pnl = max_daily_loss
            
        balance += day_pnl
        daily_records.append({
            'date': curr_d,
            'day_pnl': day_pnl,
            'pnl_pct': (day_pnl / (balance - day_pnl)) if (balance - day_pnl) > 0 else 0.0,
            'ending_balance': balance
        })
        
    df_daily = pd.DataFrame(daily_records)
    df_daily['year'] = pd.to_datetime(df_daily['date']).dt.year
    df_daily['ym'] = pd.to_datetime(df_daily['date']).dt.strftime('%Y-%m')
    
    annual = df_daily.groupby('year')['pnl_pct'].apply(lambda x: (1.0 + x).prod() - 1.0) * 100.0
    monthly = df_daily.groupby('ym')['pnl_pct'].apply(lambda x: (1.0 + x).prod() - 1.0)
    
    equity = (1.0 + df_daily['pnl_pct']).cumprod()
    max_dd = ((equity - equity.cummax()) / equity.cummax()).min() * 100.0
    
    m2026 = monthly[monthly.index.str.startswith('2026')]
    g2026 = (m2026 > 0).sum()
    t2026 = len(m2026)
    
    df_tr = pd.DataFrame(trade_records)
    if len(df_tr) > 0:
        wr = (df_tr['pnl'] > 0).sum() / len(df_tr) * 100.0
        g = df_tr[df_tr['pnl'] > 0]['pnl'].sum()
        l = abs(df_tr[df_tr['pnl'] < 0]['pnl'].sum())
        pf = g / l if l > 0 else 0.0
        avg_r = df_tr['r'].mean()
    else:
        wr = 0; pf = 0; avg_r = 0
        
    return {
        'variant': variant_name,
        'r1_pct': r1_pct * 100.0,
        'r2_pct': r2_pct * 100.0,
        'final_balance': balance,
        'avg_annual_roi': annual.mean(),
        'worst_year': annual.min(),
        'max_dd': max_dd,
        'green_months': f"{(monthly > 0).sum()}/{len(monthly)} ({(monthly > 0).sum()/len(monthly)*100:.1f}%)",
        'm2026': f"{g2026}/{t2026}",
        'roi_2026': ((1.0 + m2026).prod() - 1.0) * 100.0,
        'pf': pf,
        'wr': wr,
        'trades': len(df_tr),
        'avg_r': avg_r,
        'toll': total_toll_paid,
        'annual_dict': annual.to_dict()
    }

def main():
    print("Loading 5-year Gold sessions from MT5...")
    sessions = load_gold_sessions()
    print(f"Loaded {len(sessions)} trading sessions. Testing Pyramiding Variants under max 2.0% risk per trade...\n")
    
    test_suite = [
        # 1. Benchmark: Full 2.0% Single Entry (No Pyramiding)
        {
            'name': 'Benchmark: Single Entry (2.0% Risk, No Pyramid)',
            'r1': 0.020, 'r2': 0.000, 'trig': 1.0, 'lock': 0.0, 'rr': 2.0
        },
        # 2. Variant 1: Aggressive Equal-Weight (1.0% Initial + 1.0% Pyramid at +1.0R, SL1 to BE)
        {
            'name': 'Variant 1: Equal-Weight (1.0% Base + 1.0% at +1.0R, SL1 to BE)',
            'r1': 0.010, 'r2': 0.010, 'trig': 1.0, 'lock': 0.0, 'rr': 2.0
        },
        # 3. Variant 2: Front-Loaded (1.33% Base + 0.67% at +1.0R, SL1 to BE)
        {
            'name': 'Variant 2: Front-Loaded (1.33% Base + 0.67% at +1.0R, SL1 to BE)',
            'r1': 0.0133, 'r2': 0.0067, 'trig': 1.0, 'lock': 0.0, 'rr': 2.0
        },
        # 4. Variant 3: Guaranteed Free-Roll (1.0% Base + 0.5% at +1.0R, SL1 locked at +0.5R)
        {
            'name': 'Variant 3: Free-Roll Lock (1.0% Base + 0.5% at +1.0R, SL1 locked +0.5R)',
            'r1': 0.010, 'r2': 0.005, 'trig': 1.0, 'lock': 0.5, 'rr': 2.0
        },
        # 5. Variant 4: Early Scale-In (1.0% Base + 1.0% at +0.75R, SL1 to BE)
        {
            'name': 'Variant 4: Early Scale-In (1.0% Base + 1.0% at +0.75R, SL1 to BE)',
            'r1': 0.010, 'r2': 0.010, 'trig': 0.75, 'lock': 0.0, 'rr': 2.0
        },
        # 6. Variant 5: Deep Trend Runner (1.0% Base + 1.0% at +1.25R, RR 2.5)
        {
            'name': 'Variant 5: Deep Runner (1.0% Base + 1.0% at +1.25R, RR 2.5)',
            'r1': 0.010, 'r2': 0.010, 'trig': 1.25, 'lock': 0.5, 'rr': 2.5
        },
        # 7. Variant 6: Asymmetric Power Pyramid (1.0% Base + 1.0% at +1.0R, RR 2.5)
        {
            'name': 'Variant 6: Power Pyramid (1.0% Base + 1.0% at +1.0R, RR 2.5, SL1 to BE)',
            'r1': 0.010, 'r2': 0.010, 'trig': 1.0, 'lock': 0.0, 'rr': 2.5
        }
    ]
    
    results = []
    for t in test_suite:
        res = run_pyramiding_simulation(
            sessions,
            variant_name=t['name'],
            r1_pct=t['r1'],
            r2_pct=t['r2'],
            pyr_trigger_r=t['trig'],
            lock_sl1_r=t['lock'],
            sl_dollars=6.00,
            rr_multiplier=t['rr'],
            use_limit_entry=True
        )
        results.append(res)
        
    df_res = pd.DataFrame(results)
    
    print("=" * 155)
    print("EMPIRICAL SCORECARD: PYRAMIDING VARIANTS IN THE LIVE MARKET SIMULATOR (MAX 2% RISK PER TRADE)")
    print("=" * 155)
    print(f"{'VARIANT NAME':<55} | {'ANNUAL ROI':<12} | {'WORST YR':<10} | {'MAX DD':<10} | {'2026 ROI':<10} | {'PF':<5} | {'WIN %':<6} | {'FINAL BAL'}")
    print("-" * 155)
    for idx, r in df_res.iterrows():
        print(f"{r['variant']:<55} | {r['avg_annual_roi']:+9.2f}% | {r['worst_year']:+8.2f}% | {r['max_dd']:8.2f}% | {r['roi_2026']:+8.2f}% | {r['pf']:<5.2f} | {r['wr']:4.1f}% | ${r['final_balance']:,.2f}")
    print("=" * 155)

    print("\nDETAILED YEAR-BY-YEAR PERFORMANCE BY VARIANT:")
    for idx, r in df_res.iterrows():
        y_str = ", ".join([f"{y}: {v:+.1f}%" for y, v in r['annual_dict'].items()])
        print(f"  * {r['variant']:<55} -> {y_str}")

if __name__ == "__main__":
    main()

