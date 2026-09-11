import { useState } from 'react';
import { Image, StyleSheet, Text, TouchableOpacity } from 'react-native';
import Constants from 'expo-constants';
import { api } from '../api';
import { setToken } from '../auth';
import Banner from '../components/Banner';
import DismissKeyboard from '../components/DismissKeyboard';
import { FieldError, Input } from '../components/Field';
import SubmitButton from '../components/SubmitButton';

export default function LoginScreen({ onLoggedIn, onGoToSignup, onGoToForgot }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  // Per-field, keyed by the same names the API uses, so a 422 naming a field
  // can be dropped straight in. Separate from `error`, which is whatever the
  // server said about the attempt as a whole.
  const [problems, setProblems] = useState({});
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  // Clear a field's complaint the moment it is edited, and the banner's with
  // it. Leaving either up while somebody fixes it is how a form ends up
  // arguing with the person using it.
  function edit(setter, key) {
    return (value) => {
      setter(value);
      setError(null);
      setProblems((p) => (p[key] ? { ...p, [key]: null } : p));
    };
  }

  async function submit() {
    setError(null);
    const found = {};
    if (!email.trim()) found.email = 'Enter your email';
    if (!password) found.password = 'Enter your password';
    setProblems(found);
    if (Object.keys(found).length) return;

    setBusy(true);
    try {
      const { token } = await api('/login', {
        method: 'POST',
        body: { email: email.trim(), password },
      });
      await setToken(token);
      onLoggedIn();
    } catch (e) {
      // A wrong password names no field on purpose: the server will not say
      // which half was wrong, and neither should the form.
      if (e.fields?.length) {
        setProblems(Object.fromEntries(e.fields.map((f) => [f, true])));
      }
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <DismissKeyboard style={styles.container}>
      <Image source={require('../../assets/icon.png')} style={styles.logo} resizeMode="contain" />
      <Text style={styles.title}>{Constants.expoConfig?.name ?? 'Sign in'}</Text>
      <Banner>{error}</Banner>
      <Input
        style={styles.field}
        placeholder="Email"
        autoCapitalize="none"
        autoComplete="email"
        keyboardType="email-address"
        value={email}
        onChangeText={edit(setEmail, 'email')}
        invalid={!!problems.email}
      />
      <FieldError>{problems.email}</FieldError>
      <Input
        style={styles.field}
        placeholder="Password"
        secureTextEntry
        value={password}
        onChangeText={edit(setPassword, 'password')}
        invalid={!!problems.password}
        onSubmitEditing={submit}
      />
      <FieldError>{problems.password}</FieldError>
      <SubmitButton style={styles.button} label="Log in" busy={busy} onPress={submit} />
      <TouchableOpacity onPress={onGoToForgot}>
        <Text style={styles.link}>Forgot password?</Text>
      </TouchableOpacity>
      <TouchableOpacity onPress={onGoToSignup}>
        <Text style={[styles.link, styles.signupLink]}>No account? Sign up</Text>
      </TouchableOpacity>
    </DismissKeyboard>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, justifyContent: 'center', padding: 24 },
  logo: { width: 96, height: 96, borderRadius: 20, alignSelf: 'center', marginBottom: 12 },
  title: { fontSize: 32, fontWeight: '700', textAlign: 'center', marginBottom: 32, color: '#1f2937' },
  field: { marginBottom: 12 },
  button: { marginTop: 4 },
  link: { color: '#1f2937', textAlign: 'center', marginTop: 16, fontSize: 15 },
  signupLink: { marginTop: 12 },
});
