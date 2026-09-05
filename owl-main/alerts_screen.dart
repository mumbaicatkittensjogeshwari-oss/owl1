import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../providers/app_provider.dart';
import '../widgets/glass_card.dart';

class AlertsScreen extends StatelessWidget {
  const AlertsScreen({super.key});

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
                '⚠️ Alerts',
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
                    final alerts = provider.alerts;
                    if (alerts.isEmpty) {
                      return const Center(
                        child: Text(
                          'No alerts yet',
                          style: TextStyle(color: Colors.grey),
                        ),
                      );
                    }
                    return ListView.builder(
                      itemCount: alerts.length,
                      itemBuilder: (context, index) {
                        final alert = alerts[index];
                        final text = alert['text'] ?? '--';
                        final time = alert['time'] ?? '--';
                        final type = alert['type'] ?? 'info';

                        Color color = Colors.grey;
                        if (type == 'tp' || type == 'open' || type == 'target') {
                          color = const Color(0xFF00BFA0);
                        } else if (type == 'sl' || type == 'loss' || type == 'error') {
                          color = Colors.red;
                        } else if (type == 'reversed' || type == 'skip') {
                          color = Colors.amber;
                        } else {
                          color = Colors.blue;
                        }

                        return Padding(
                          padding: const EdgeInsets.only(bottom: 6),
                          child: GlassCard(
                            child: Padding(
                              padding: const EdgeInsets.all(10),
                              child: Row(
                                children: [
                                  Container(
                                    width: 4,
                                    height: 30,
                                    color: color,
                                  ),
                                  const SizedBox(width: 10),
                                  Expanded(
                                    child: Text(
                                      text,
                                      style: const TextStyle(
                                        color: Colors.white,
                                        fontSize: 13,
                                      ),
                                    ),
                                  ),
                                  Text(
                                    time,
                                    style: TextStyle(
                                      color: Colors.grey.shade500,
                                      fontSize: 11,
                                    ),
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