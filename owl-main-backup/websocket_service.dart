import 'dart:convert';
import 'package:web_socket_channel/web_socket_channel.dart';
import 'package:web_socket_channel/status.dart' as status;

class WebSocketService {
  WebSocketChannel? _channel;
  Function(Map<String, dynamic>)? onMessage;
  bool _isConnected = false;
  Timer? _reconnectTimer;
  String _wsUrl = 'ws://localhost:8000/ws';

  void connect() {
    try {
      _channel = WebSocketChannel.connect(
        Uri.parse(_wsUrl),
        pingInterval: const Duration(seconds: 30),
      );
      
      _channel!.stream.listen(
        (message) {
          try {
            final data = jsonDecode(message);
            if (data['type'] == 'pong') {
              // Keep-alive
              return;
            }
            if (onMessage != null) {
              onMessage!(data);
            }
            _isConnected = true;
          } catch (e) {
            print('WebSocket parse error: $e');
          }
        },
        onDone: () {
          _isConnected = false;
          _scheduleReconnect();
        },
        onError: (error) {
          print('WebSocket error: $error');
          _isConnected = false;
          _scheduleReconnect();
        },
      );
      
      _isConnected = true;
      print('WebSocket connected');
    } catch (e) {
      print('WebSocket connection error: $e');
      _scheduleReconnect();
    }
  }

  void _scheduleReconnect() {
    _reconnectTimer?.cancel();
    _reconnectTimer = Timer(const Duration(seconds: 3), () {
      print('Reconnecting WebSocket...');
      connect();
    });
  }

  void disconnect() {
    _reconnectTimer?.cancel();
    if (_channel != null) {
      _channel!.sink.close(status.goingAway);
      _channel = null;
    }
    _isConnected = false;
  }

  bool get isConnected => _isConnected;

  void sendPing() {
    if (_channel != null && _isConnected) {
      _channel!.sink.add(jsonEncode({'type': 'ping'}));
    }
  }
}