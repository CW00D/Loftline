import { useState } from 'react';
import {
  Alert,
  Image,
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
} from 'react-native';
import { api } from '../api';
import Banner from '../components/Banner';
import { FieldError, Input } from '../components/Field';
import SubmitButton from '../components/SubmitButton';

// The server answers /forgot-password identically whether or not the address
// is on an account, so this copy has to be true in both cases.
const SENT_NOTE = "If that email is on an account, a 6-digit code is on its way. It's good for an hour.";

export default function ForgotPasswordScreen({ onDone, onBack }) {
  const [step, setStep] = useState('email'); // 'email' | 'code'
  const [email, setEmail] = useState('');
  const [code, setCode] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [problems, setProblems] = useState({});
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  function edit(setter, key) {
    return (value) => {
      setter(value);
      setError(null);
      setProblems((p) => (p[key] ? { ...p, [key]: null } : p));
    };
  }

  async function requestCode({ resend = false } = {}) {
    setError(null);
    const found = {};
    if (!email.trim()) found.email = 'Enter the email you signed up with';
    else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())) {
      found.email = "That doesn't look like an email address";
    }
    setProblems(found);
    if (Object.keys(found).length) return;

    setBusy(true);
    try {
      await api('/forgot-password', {
        method: 'POST',
        body: { email: email.trim().toLowerCase() },
      });
      setStep('code');
      if (resend) Alert.alert('Code sent', SENT_NOTE);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function submitReset() {
    setError(null);
    const found = {};
    if (code.length !== 6) found.code = 'The code is six digits';
    if (!password) found.password = 'Choose a new password';
    else if (password.length < 8) found.password = 'At least 8 characters';
    if (!confirmPassword) found.confirmPassword = 'Confirm your new password';
    else if (password !== confirmPassword) {
      found.confirmPassword = 'Passwords do not match';
    }
    setProblems(found);
    if (Object.keys(found).length) return;

    setBusy(true);
    try {
      await api('/reset-password', {
        method: 'POST',
        body: { email: email.trim().toLowerCase(), code: code.trim(), new_password: password },
      });
      Alert.alert('Password reset', 'Log in with your new password.');
      onDone();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
      <ScrollView
        contentContainerStyle={styles.container}
        keyboardShouldPersistTaps="handled"
        showsVerticalScrollIndicator={false}
      >
        <Image source={require('../../assets/icon.png')} style={styles.logo} resizeMode="contain" />
        <Text style={styles.title}>Reset password</Text>
        <Banner>{error}</Banner>

        {step === 'email' ? (
          <>
            <Text style={styles.blurb}>Enter the email you signed up with and we'll send you a code.</Text>
            <Input
              style={styles.field}
              placeholder="Email"
              autoCapitalize="none"
              autoComplete="email"
              keyboardType="email-address"
              value={email}
              onChangeText={edit(setEmail, 'email')}
              invalid={!!problems.email}
              onSubmitEditing={() => requestCode()}
              autoFocus
            />
            <FieldError>{problems.email}</FieldError>
            <SubmitButton style={styles.button} label="Send code" busy={busy} onPress={() => requestCode()} />
          </>
        ) : (
          <>
            <Text style={styles.blurb}>{SENT_NOTE}</Text>
            <Input
              style={[styles.field, styles.codeInput]}
              placeholder="000000"
              keyboardType="number-pad"
              maxLength={6}
              value={code}
              onChangeText={edit((t) => setCode(t.replace(/\D/g, '')), 'code')}
              invalid={!!problems.code}
              autoFocus
            />
            <FieldError>{problems.code}</FieldError>
            <Input
              style={styles.field}
              placeholder="New password (min 8 characters)"
              secureTextEntry
              value={password}
              onChangeText={edit(setPassword, 'password')}
              invalid={!!problems.password}
            />
            <FieldError>{problems.password}</FieldError>
            <Input
              style={styles.field}
              placeholder="Confirm new password"
              secureTextEntry
              value={confirmPassword}
              onChangeText={edit(setConfirmPassword, 'confirmPassword')}
              invalid={!!problems.confirmPassword}
            />
            <FieldError>{problems.confirmPassword}</FieldError>
            <SubmitButton style={styles.button} label="Set new password" busy={busy} onPress={submitReset} />
            <TouchableOpacity onPress={() => requestCode({ resend: true })} disabled={busy}>
              <Text style={styles.link}>Didn't get it? Send another</Text>
            </TouchableOpacity>
          </>
        )}

        <TouchableOpacity onPress={onBack}>
          <Text style={styles.link}>Back to log in</Text>
        </TouchableOpacity>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  container: { flexGrow: 1, justifyContent: 'center', padding: 24 },
  logo: { width: 96, height: 96, borderRadius: 20, alignSelf: 'center', marginBottom: 12 },
  title: { fontSize: 28, fontWeight: '700', textAlign: 'center', marginBottom: 12, color: '#1f2937' },
  blurb: { fontSize: 14, lineHeight: 20, color: '#666', textAlign: 'center', marginBottom: 20 },
  field: { marginBottom: 12 },
  codeInput: { fontSize: 26, letterSpacing: 8, textAlign: 'center', fontWeight: '600' },
  button: { marginTop: 4 },
  link: { color: '#1f2937', textAlign: 'center', marginTop: 16, fontSize: 15 },
});
