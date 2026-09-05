import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:http/http.dart' as http;
import 'dart:convert';

class AppProvider extends ChangeNotifier {
  final SharedPreferences prefs;
  
  AppProvider(this.prefs);

  // State
  Map<String, dynamic> _status = {};
  List<dynamic> _positions = [];
  List<dynamic> _movers = [];
  List<dynamic> _signals = [];
  List<dynamic> _alerts = [];
  List<dynamic> _history = [];
  List<Map<String, dynamic>> _equityCurve = [];
  Map<String, dynamic> _settings = {};
  
  bool _isLoading = false;
  String? _error;

  // Getters
  Map<String, dynamic> get status => _status;
  List<dynamic> get positions => _positions;
  List<dynamic> get movers => _movers;
  List<dynamic> get signals => _signals;
  List<dynamic> get alerts => _alerts;
  List<dynamic> get history => _history;
  List<Map<String, dynamic>> get equityCurve => _equityCurve;
  Map<String, dynamic> get settings => _settings;
  bool get isLoading => _isLoading;
  String? get error => _error;
  
  // API URL - change to your backend IP/domain
  String get _baseUrl => 'http://localhost:8000';

  Future<void> init() async {
    await Future.wait([
      fetchStatus(),
      fetchMarket(),
      fetchSignals(),
      fetchAlerts(),
      fetchHistory(),
      fetchEquity(),
      fetchSettings(),
    ]);
    notifyListeners();
  }

  Future<void> fetchStatus() async {
    try {
      final response = await http.get(Uri.parse('$_baseUrl/api/status'));
      if (response.statusCode == 200) {
        _status = jsonDecode(response.body);
        _positions = _status['open_positions'] ?? [];
        notifyListeners();
      }
    } catch (e) {
      _error = e.toString();
      notifyListeners();
    }
  }

  Future<void> fetchMarket() async {
    try {
      final response = await http.get(Uri.parse('$_baseUrl/api/market'));
      if (response.statusCode == 200) {
        final data = jsonDecode(response.body);
        _movers = data['movers'] ?? [];
        notifyListeners();
      }
    } catch (e) {
      _error = e.toString();
      notifyListeners();
    }
  }

  Future<void> fetchSignals() async {
    try {
      final response = await http.get(Uri.parse('$_baseUrl/api/signals'));
      if (response.statusCode == 200) {
        final data = jsonDecode(response.body);
        _signals = data['signals'] ?? [];
        notifyListeners();
      }
    } catch (e) {
      _error = e.toString();
      notifyListeners();
    }
  }

  Future<void> fetchAlerts() async {
    try {
      final response = await http.get(Uri.parse('$_baseUrl/api/alerts'));
      if (response.statusCode == 200) {
        final data = jsonDecode(response.body);
        _alerts = data['alerts'] ?? [];
        notifyListeners();
      }
    } catch (e) {
      _error = e.toString();
      notifyListeners();
    }
  }

  Future<void> fetchHistory() async {
    try {
      final response = await http.get(Uri.parse('$_baseUrl/api/history'));
      if (response.statusCode == 200) {
        final data = jsonDecode(response.body);
        _history = data['trades'] ?? [];
        notifyListeners();
      }
    } catch (e) {
      _error = e.toString();
      notifyListeners();
    }
  }

  Future<void> fetchEquity() async {
    try {
      final response = await http.get(Uri.parse('$_baseUrl/api/equity'));
      if (response.statusCode == 200) {
        final data = jsonDecode(response.body);
        _equityCurve = List<Map<String, dynamic>>.from(data['curve'] ?? []);
        notifyListeners();
      }
    } catch (e) {
      _error = e.toString();
      notifyListeners();
    }
  }

  Future<void> fetchSettings() async {
    try {
      final response = await http.get(Uri.parse('$_baseUrl/api/settings'));
      if (response.statusCode == 200) {
        _settings = jsonDecode(response.body);
        notifyListeners();
      }
    } catch (e) {
      _error = e.toString();
      notifyListeners();
    }
  }

  Future<void> updateSettings(Map<String, dynamic> newSettings) async {
    try {
      final response = await http.post(
        Uri.parse('$_baseUrl/api/settings'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode(newSettings),
      );
      if (response.statusCode == 200) {
        _settings.addAll(newSettings);
        notifyListeners();
      }
    } catch (e) {
      _error = e.toString();
      notifyListeners();
    }
  }

  Future<void> closePosition(int id) async {
    try {
      final response = await http.post(
        Uri.parse('$_baseUrl/api/close/$id'),
      );
      if (response.statusCode == 200) {
        await fetchStatus();
      }
    } catch (e) {
      _error = e.toString();
      notifyListeners();
    }
  }

  Future<void> closeAllPositions() async {
    try {
      final response = await http.post(
        Uri.parse('$_baseUrl/api/close_all'),
      );
      if (response.statusCode == 200) {
        await fetchStatus();
      }
    } catch (e) {
      _error = e.toString();
      notifyListeners();
    }
  }

  void updateFromWebSocket(Map<String, dynamic> data) {
    if (data['type'] == 'update' || data['type'] == 'init') {
      final payload = data['data'] ?? data;
      if (payload is Map<String, dynamic>) {
        if (payload.containsKey('open_positions')) {
          _positions = payload['open_positions'] ?? [];
        }
        if (payload.containsKey('market_movers')) {
          _movers = payload['market_movers'] ?? [];
        }
        if (payload.containsKey('today_pnl_usdt')) {
          _status = payload;
        }
        notifyListeners();
      }
    }
  }
}