import 'package:flutter/material.dart';
import 'package:fl_chart/fl_chart.dart';

class KLineChart extends StatelessWidget {
  final List<Map<String, dynamic>> data;
  final String symbol;
  
  const KLineChart({
    Key? key, 
    required this.data,
    this.symbol = 'BTC/USDT',
  }) : super(key: key);

  @override
  Widget build(BuildContext context) {
    // Agar data empty hai toh placeholder dikhao
    if (data.isEmpty) {
      return Container(
        height: 200,
        decoration: BoxDecoration(
          color: Colors.grey[900],
          borderRadius: BorderRadius.circular(12),
        ),
        child: Center(
          child: Text(
            'No data available',
            style: TextStyle(color: Colors.grey[500]),
          ),
        ),
      );
    }

    // Data ko LineChart ke liye convert karo
    final spots = _getSpots();
    final minPrice = _getMinPrice();
    final maxPrice = _getMaxPrice();

    return Container(
      height: 200,
      padding: const EdgeInsets.all(8),
      decoration: BoxDecoration(
        color: Colors.grey[900],
        borderRadius: BorderRadius.circular(12),
      ),
      child: LineChart(
        LineChartData(
          gridData: FlGridData(
            show: true,
            drawHorizontalLine: true,
            drawVerticalLine: false,
            horizontalInterval: (maxPrice - minPrice) / 4,
          ),
          titlesData: FlTitlesData(
            show: true,
            bottomTitles: AxisTitles(
              sideTitles: SideTitles(
                showTitles: true,
                reservedSize: 30,
                getTitlesWidget: (value, meta) {
                  // Show every 5th point
                  if (value.toInt() % 5 == 0 && value < spots.length) {
                    return Padding(
                      padding: const EdgeInsets.only(top: 4),
                      child: Text(
                        '${spots[value.toInt()].x}',
                        style: TextStyle(
                          color: Colors.grey[500],
                          fontSize: 10,
                        ),
                      ),
                    );
                  }
                  return const SizedBox.shrink();
                },
              ),
            ),
            leftTitles: AxisTitles(
              sideTitles: SideTitles(
                showTitles: true,
                reservedSize: 40,
                getTitlesWidget: (value, meta) {
                  return Text(
                    '\$${value.toStringAsFixed(2)}',
                    style: TextStyle(
                      color: Colors.grey[500],
                      fontSize: 10,
                    ),
                  );
                },
              ),
            ),
            topTitles: AxisTitles(
              sideTitles: SideTitles(showTitles: false),
            ),
            rightTitles: AxisTitles(
              sideTitles: SideTitles(showTitles: false),
            ),
          ),
          borderData: FlBorderData(
            show: true,
            border: Border.all(color: Colors.grey[800]!, width: 1),
          ),
          minX: 0,
          maxX: spots.length.toDouble() - 1,
          minY: minPrice * 0.95,
          maxY: maxPrice * 1.05,
          lineBarsData: [
            LineChartBarData(
              spots: spots,
              isCurved: true,
              color: _getLineColor(),
              barWidth: 2,
              isStrokeCapRound: true,
              dotData: FlDotData(show: false),
              belowBarData: BarAreaData(
                show: true,
                color: _getLineColor().withOpacity(0.2),
              ),
            ),
          ],
        ),
      ),
    );
  }

  // Helper methods
  List<FlSpot> _getSpots() {
    return data.asMap().entries.map((entry) {
      int index = entry.key;
      double price = (entry.value['close'] ?? entry.value['price'] ?? 0).toDouble();
      return FlSpot(index.toDouble(), price);
    }).toList();
  }

  double _getMinPrice() {
    return data.map((e) => (e['close'] ?? e['price'] ?? 0).toDouble()).reduce((a, b) => a < b ? a : b);
  }

  double _getMaxPrice() {
    return data.map((e) => (e['close'] ?? e['price'] ?? 0).toDouble()).reduce((a, b) => a > b ? a : b);
  }

  Color _getLineColor() {
    if (data.isEmpty) return Colors.blue;
    final firstPrice = (data.first['close'] ?? data.first['price'] ?? 0).toDouble();
    final lastPrice = (data.last['close'] ?? data.last['price'] ?? 0).toDouble();
    return lastPrice >= firstPrice ? Colors.green : Colors.red;
  }
}
