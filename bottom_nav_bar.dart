import 'package:flutter/material.dart';
import 'package:animated_bottom_navigation_bar/animated_bottom_navigation_bar.dart';

class BottomNavBar extends StatelessWidget {
  final int currentIndex;
  final Function(int) onTap;

  const BottomNavBar({
    super.key,
    required this.currentIndex,
    required this.onTap,
  });

  final List<IconData> _icons = const [
    Icons.dashboard,
    Icons.trending_up,
    Icons.shopping_bag,
    Icons.notifications,
    Icons.warning,
    Icons.settings,
  ];

  final List<String> _labels = const [
    'Dash',
    'Market',
    'Pos',
    'Signals',
    'Alerts',
    'Settings',
  ];

  @override
  Widget build(BuildContext context) {
    return AnimatedBottomNavigationBar(
      icons: _icons,
      activeIndex: currentIndex,
      gapLocation: GapLocation.center,
      notchSmoothness: NotchSmoothness.softEdge,
      leftCornerRadius: 16,
      rightCornerRadius: 16,
      iconSize: 24,
      backgroundColor: const Color(0xFF12121A),
      activeColor: const Color(0xFF00BFA0),
      inactiveColor: Colors.grey.shade600,
      onTap: onTap,
    );
  }
}