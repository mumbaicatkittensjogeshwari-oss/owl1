import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../providers/app_provider.dart';
import '../widgets/glass_card.dart';

class PositionsScreen extends StatelessWidget {
  const PositionsScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text(
                '💼 Positions',
                style: TextStyle(
                  fontSize: 24,
                  fontWeight: FontWeight.bold,
                  color: Colors.white,
                ),
              ),
              const SizedBox(height: 16),
              Expanded(
                child: Consumer<AppProvider>(
                  builder: (context, provider, child) {
                    final positions = provider.positions;
                    if (positions.isEmpty) {
                      return const Center(
                        child: Text(
                          'No open positions',
                          style: TextStyle(color: Colors.grey),
                        ),
                      );
                    }
                    return ListView.builder(
                      itemCount: positions.length,
                      itemBuilder: (context, index) {
                        final pos = positions[index];
                        final symbol = pos['symbol'] ?? '--';
                        final action = pos['action'] ?? '--';
                        final entry = pos['entry_price'] ?? 0.0;
                        final ltp = pos['ltp'] ?? 0.0;
                        final pnl = pos['pnl_usdt'] ?? 0.0;
                        final roi = pos['roi_pct'] ?? 0.0;
                        final isLong = action == 'BUY';
                        final isProfitable = pnl >= 0;

                        return Padding(
                          padding: const EdgeInsets.only(bottom: 8),
                          child: GlassCard(
                            child: Padding(
                              padding: const EdgeInsets.all(12),
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Row(
                                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                                    children: [
                                      Row(
                                        children: [
                                          Container(
                                            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                                            decoration: BoxDecoration(
                                              color: isLong ? Colors.green.withOpacity(0.2) : Colors.red.withOpacity(0.2),
                                              borderRadius: BorderRadius.circular(8),
                                            ),
                                            child: Text(
                                              action,
                                              style: TextStyle(
                                                color: isLong ? Colors.green : Colors.red,
                                                fontWeight: FontWeight.bold,
                                                fontSize: 12,
                                              ),
                                            ),
                                          ),
                                          const SizedBox(width: 8),
                                          Text(
                                            symbol,
                                            style: const TextStyle(
                                              color: Colors.white,
                                              fontWeight: FontWeight.bold,
                                              fontSize: 16,
                                            ),
                                          ),
                                        ],
                                      ),
                                      Text(
                                        '${isProfitable ? '+' : ''}${pnl.toStringAsFixed(2)} USDT',
                                        style: TextStyle(
                                          color: isProfitable ? Colors.green : Colors.red,
                                          fontWeight: FontWeight.bold,
                                          fontSize: 16,
                                        ),
                                      ),
                                    ],
                                  ),
                                  const SizedBox(height: 4),
                                  Row(
                                    children: [
                                      Text(
                                        'Entry: ${entry.toStringAsFixed(4)}  |  LTP: ${ltp.toStringAsFixed(4)}',
                                        style: TextStyle(color: Colors.grey.shade400, fontSize: 12),
                                      ),
                                      const Spacer(),
                                      Text(
                                        'ROI: ${roi.toStringAsFixed(2)}%',
                                        style: TextStyle(
                                          color: isProfitable ? Colors.green : Colors.red,
                                          fontWeight: FontWeight.bold,
                                          fontSize: 12,
                                        ),
                                      ),
                                    ],
                                  ),
                                ],
                              ),
                            ),
                          ),
                        );
                      },
                    );
                  },
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}