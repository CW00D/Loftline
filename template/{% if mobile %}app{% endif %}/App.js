import { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';
import { StatusBar } from 'expo-status-bar';
import { api, onUnauthorized } from './src/api';
import { clearToken, getToken } from './src/auth';
import ForgotPasswordScreen from './src/screens/ForgotPasswordScreen';
import HomeScreen from './src/screens/HomeScreen';
import LoginScreen from './src/screens/LoginScreen';
import SignupScreen from './src/screens/SignupScreen';

export default function App() {
  // 'loading' | 'offline' | 'login' | 'signup' | 'forgot' | 'home'
  const [screen, setScreen] = useState('loading');

  // Resume a stored session. Named rather than inline in the effect so the
  // offline screen's Retry runs this exact check instead of a copy of it.
  const resume = useCallback(async () => {
    setScreen('loading');
    const token = await getToken();
    if (!token) {
      setScreen('login');
      return;
    }
    try {
      await api('/me');
      setScreen('home');
    } catch (e) {
      // Only the server rejecting the token ends the session. A network
      // failure keeps the token and offers a retry: the token is fine, the
      // connection is not, and making somebody retype a password over that
      // is the worst thing an app can do.
      if (e.status === 401) {
        await clearToken();
        setScreen('login');
        return;
      }
      setScreen('offline');
    }
  }, []);

  useEffect(() => {
    resume();
  }, [resume]);

  // A token that expires mid-session 401s on whatever screen the user is on.
  // api() has already dropped it by then; this routes back to login once.
  useEffect(() => {
    onUnauthorized(() => setScreen('login'));
    return () => onUnauthorized(null);
  }, []);

  if (screen === 'loading') {
    return (
      <View style={{ flex: 1, justifyContent: 'center' }}>
        <ActivityIndicator size="large" />
      </View>
    );
  }

  if (screen === 'offline') {
    return (
      <View style={styles.offline}>
        <StatusBar style="auto" />
        <Text style={styles.offlineTitle}>Couldn't reach the server</Text>
        <Text style={styles.offlineText}>
          You're still signed in. This is the connection, not your account.
          Try again once you have signal.
        </Text>
        <TouchableOpacity style={styles.retry} onPress={resume}>
          <Text style={styles.retryText}>Retry</Text>
        </TouchableOpacity>
      </View>
    );
  }

  return (
    <View style={{ flex: 1 }}>
      <StatusBar style="auto" />
      {screen === 'login' && (
        <LoginScreen
          onLoggedIn={() => setScreen('home')}
          onGoToSignup={() => setScreen('signup')}
          onGoToForgot={() => setScreen('forgot')}
        />
      )}
      {screen === 'signup' && (
        <SignupScreen
          onSignedUp={() => setScreen('home')}
          onGoToLogin={() => setScreen('login')}
        />
      )}
      {/* Both exits land on login: a reset never issues a token, so the new
          password still has to be typed once. */}
      {screen === 'forgot' && (
        <ForgotPasswordScreen
          onDone={() => setScreen('login')}
          onBack={() => setScreen('login')}
        />
      )}
      {screen === 'home' && (
        <HomeScreen
          onLoggedOut={async () => {
            await clearToken();
            setScreen('login');
          }}
        />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  offline: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 32 },
  offlineTitle: { fontSize: 20, fontWeight: '700', marginBottom: 10, textAlign: 'center' },
  offlineText: { fontSize: 15, color: '#666', textAlign: 'center', marginBottom: 24 },
  retry: {
    backgroundColor: '#1f2937',
    borderRadius: 10,
    paddingVertical: 12,
    paddingHorizontal: 32,
  },
  retryText: { color: '#fff', fontSize: 16, fontWeight: '600' },
});
