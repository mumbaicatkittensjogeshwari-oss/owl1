import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'providers/app_provider.dart';
import 'screens/dashboard_screen.dart';
import 'screens/market_screen.dart';
import 'screens/positions_screen.dart';
import 'screens/signals_screen.dart';
import 'screens/alerts_screen.dart';
import 'screens/settings_screen.dart';
import 'widgets/bottom_nav_bar.dart';
import 'services/websocket_service.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  final prefs = await SharedPreferences.getInstance();
  runApp(TradingBotApp(prefs: prefs));
}

class TradingBotApp extends StatelessWidget {
  final SharedPreferences prefs;

  const TradingBotApp({super.key, required this.prefs});

  @override
  Widget build(BuildContext context) {
    return ChangeNotifierProvider(
      create: (context) => AppProvider(prefs)..init(),
      child: Consumer<AppProvider>(
        builder: (context, provider, child) {
          return MaterialApp(
            title: 'Trading Bot',
            theme: ThemeData.dark().copyWith(
              scaffoldBackgroundColor: const Color(0xFF0A0A12),
              primaryColor: const Color(0xFF00BFA0),
              colorScheme: const ColorScheme.dark(
                primary: Color(0xFF00BFA0),
                secondary: Color(0xFF00BFA0),
              ),
              textTheme: const TextTheme(
                bodyMedium: TextStyle(fontFamily: 'Inter'),
                titleMedium: TextStyle(fontFamily: 'Inter'),
              ),
            ),
            home: const MainScreen(),
            debugShowCheckedModeBanner: false,
          );
        },
      ),
    );
  }
}

class MainScreen extends StatefulWidget {
  const MainScreen({super.key});

  @override
  State<MainScreen> createState() => _MainScreenState();
}

class _MainScreenState extends State<MainScreen> {
  int _currentIndex = 0;
  late WebSocketService _wsService;

  @override
  void initState() {
    super.initState();
    _wsService = WebSocketService();
    _wsService.connect();
  }

  @override
  void dispose() {
    _wsService.disconnect();
    super.dispose();
  }

  final List<Widget> _screens = [
    const DashboardScreen(),
    const MarketScreen(),
    const PositionsScreen(),
    const SignalsScreen(),
    const AlertsScreen(),
    const SettingsScreen(),
  ];

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: _screens[_currentIndex],
      bottomNavigationBar: BottomNavBar(
        currentIndex: _currentIndex,
        onTap: (index) {
          setState(() {
            _currentIndex = index;
          });
        },
      ),
    );
  }
}
