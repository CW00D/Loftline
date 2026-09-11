import { useEffect, useState } from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { api } from '../api';
import SubmitButton from '../components/SubmitButton';
import { registerForPush } from '../notifications';

// Where the app lands once signed in. Deliberately nothing here: the product
// starts on this screen. It proves the round trip (a stored token, /me, a
// name on screen) and gives the session a way out.
export default function HomeScreen({ onLoggedOut }) {
  const [me, setMe] = useState(null);

  useEffect(() => {
    api('/me').then(setMe).catch(() => {});
    // Best-effort in every direction; see src/notifications.js.
    registerForPush();
  }, []);

  return (
    <View style={styles.container}>
      <Text style={styles.title}>Signed in</Text>
      <Text style={styles.body}>{me ? `${me.name} (@${me.handle})` : ' '}</Text>
      <SubmitButton style={styles.button} label="Log out" onPress={onLoggedOut} />
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, justifyContent: 'center', padding: 24 },
  title: { fontSize: 28, fontWeight: '700', textAlign: 'center', marginBottom: 8, color: '#1f2937' },
  body: { fontSize: 16, textAlign: 'center', color: '#666', marginBottom: 32 },
  button: { marginTop: 4 },
});
