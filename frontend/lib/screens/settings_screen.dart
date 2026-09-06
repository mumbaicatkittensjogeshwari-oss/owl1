import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../providers/app_provider.dart';
import '../widgets/glass_card.dart';

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key});

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  final Map<String, TextEditingController> _controllers = {};
  bool _isLoading = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      final provider = Provider.of<AppProvider>(context, listen: false);
      final settings = provider.settings;
      _controllers['invest_amount'] = TextEditingController(
        text: (settings['invest_amount'] ?? 10.0).toString(),
      );
      _controllers['leverage'] = TextEditingController(
        text: (settings['leverage'] ?? 1).toString(),
      );
      _controllers['daily_target_usdt'] = TextEditingController(
        text: (settings['daily_target_usdt'] ?? 0.0).toString(),
      );
      _controllers['daily_loss_limit_usdt'] = TextEditingController(
        text: (settings['daily_loss_limit_usdt'] ?? 0.0).toString(),
      );
      _controllers['telegram_bot_token'] = TextEditingController(
        text: (settings['telegram_bot_token'] ?? '').toString(),
      );
      _controllers['telegram_chat_id'] = TextEditingController(
        text: (settings['telegram_chat_id'] ?? '').toString(),
      );
    });
  }

  @override
  void dispose() {
    _controllers.values.forEach((c) => c.dispose());
    super.dispose();
  }

  Future<void> _saveSettings() async {
    setState(() => _isLoading = true);
    final settings = {
      'invest_amount': double.tryParse(_controllers['invest_amount']?.text ?? '10') ?? 10.0,
      'leverage': double.tryParse(_controllers['leverage']?.text ?? '1') ?? 1.0,
      'daily_target_usdt': double.tryParse(_controllers['daily_target_usdt']?.text ?? '0') ?? 0.0,
      'daily_loss_limit_usdt': double.tryParse(_controllers['daily_loss_limit_usdt']?.text ?? '0') ?? 0.0,
      'telegram_bot_token': _controllers['telegram_bot_token']?.text ?? '',
      'telegram_chat_id': _controllers['telegram_chat_id']?.text ?? '',
    };
    await Provider.of<AppProvider>(context, listen: false).updateSettings(settings);
    setState(() => _isLoading = false);
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(content: Text('Settings saved!')),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: Column(
          children: [
            const Padding(
              padding: EdgeInsets.all(16),
              child: Row(
                children: [
                  Text(
                    '⚙️ Settings',
                    style: TextStyle(
                      fontSize: 24,
                      fontWeight: FontWeight.bold,
                      color: Colors.white,
                    ),
                  ),
                ],
              ),
            ),
            Expanded(
              child: SingleChildScrollView(
                padding: const EdgeInsets.all(16),
                child: Column(
                  children: [
                    _buildSection('Trading', [
                      _buildTextField('Invest Amount (USDT)', 'invest_amount'),
                      _buildTextField('Leverage', 'leverage'),
                    ]),
                    const SizedBox(height: 16),
                    _buildSection('Daily Limits', [
                      _buildTextField('Daily Target (USDT)', 'daily_target_usdt'),
                      _buildTextField('Daily Loss Limit (USDT)', 'daily_loss_limit_usdt'),
                    ]),
                    const SizedBox(height: 16),
                    _buildSection('Telegram', [
                      _buildTextField('Bot Token', 'telegram_bot_token', isText: true),
                      _buildTextField('Chat ID', 'telegram_chat_id', isText: true),
                    ]),
                    const SizedBox(height: 24),
                    ElevatedButton(
                      onPressed: _isLoading ? null : _saveSettings,
                      style: ElevatedButton.styleFrom(
                        backgroundColor: const Color(0xFF00BFA0),
                        minimumSize: const Size(double.infinity, 50),
                        shape: RoundedRectangleBorder(
                          borderRadius: BorderRadius.circular(12),
                        ),
                      ),
                      child: Text(
                        _isLoading ? 'Saving...' : '💾 Save Settings',
                        style: const TextStyle(
                          color: Colors.white,
                          fontSize: 16,
                          fontWeight: FontWeight.bold,
                        ),
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildSection(String title, List<Widget> children) {
    return GlassCard(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              title,
              style: const TextStyle(
                color: Color(0xFF00BFA0),
                fontWeight: FontWeight.bold,
                fontSize: 18,
              ),
            ),
            const SizedBox(height: 8),
            ...children,
          ],
        ),
      ),
    );
  }

  Widget _buildTextField(String label, String key, {bool isText = false}) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            label,
            style: TextStyle(color: Colors.grey.shade300, fontSize: 14),
          ),
          const SizedBox(height: 4),
          TextField(
            controller: _controllers[key],
            style: const TextStyle(color: Colors.white),
            keyboardType: isText ? TextInputType.text : TextInputType.number,
            obscureText: isText && key == 'telegram_bot_token',
            decoration: InputDecoration(
              filled: true,
              fillColor: Colors.white.withOpacity(0.05),
              border: OutlineInputBorder(
                borderRadius: BorderRadius.circular(8),
                borderSide: BorderSide.none,
              ),
              contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
            ),
          ),
        ],
      ),
    );
  }
}
