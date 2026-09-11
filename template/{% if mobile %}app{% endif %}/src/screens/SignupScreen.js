import { useState } from 'react';
import {
  Image,
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
} from 'react-native';
import { api } from '../api';
import { setToken } from '../auth';
import Banner from '../components/Banner';
import { FieldError, Input } from '../components/Field';
import SubmitButton from '../components/SubmitButton';

export default function SignupScreen({ onSignedUp, onGoToLogin }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [handle, setHandle] = useState('');
  const [name, setName] = useState('');
  // Keyed by the API's own field names, so a 422 or the 409 on a taken handle
  // can be pointed at the right box.
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

  async function submit() {
    setError(null);
    // Every rule the server applies, applied here first, so "min 8
    // characters" arrives as a sentence under the box rather than as a 422
    // the form cannot explain. A taken handle still comes back from the
    // server and is flagged the same way.
    const found = {};
    if (!name.trim()) found.name = 'Enter your name';
    if (!handle.trim()) found.handle = 'Pick a handle';
    else if (!/^[a-z0-9_]{2,30}$/.test(handle.trim().toLowerCase())) {
      found.handle = 'Letters, numbers and underscores only, 2 to 30 characters';
    }
    if (!email.trim()) found.email = 'Enter your email';
    else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())) {
      found.email = "That doesn't look like an email address";
    }
    if (!password) found.password = 'Choose a password';
    else if (password.length < 8) found.password = 'At least 8 characters';
    if (!confirmPassword) found.confirmPassword = 'Confirm your password';
    else if (password !== confirmPassword) {
      found.confirmPassword = 'Passwords do not match';
    }
    setProblems(found);
    if (Object.keys(found).length) return;

    setBusy(true);
    try {
      const { token } = await api('/signup', {
        method: 'POST',
        body: {
          email: email.trim(),
          password,
          handle: handle.trim().toLowerCase(),
          name: name.trim(),
        },
      });
      await setToken(token);
      onSignedUp();
    } catch (e) {
      // Two of the server's refusals are about one specific box. Matched on
      // the message because the API answers them as a plain 409 rather than
      // a field-shaped 422; `fields` covers the 422 case.
      const field =
        e.fields?.[0] ??
        (e.status === 409 && /handle/i.test(e.message) ? 'handle' : null) ??
        (e.status === 409 && /email/i.test(e.message) ? 'email' : null);
      if (field) setProblems({ [field]: e.message });
      else setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  // A ScrollView, because five fields each able to grow a line of red
  // underneath do not fit on a small phone, and a form you cannot reach the
  // bottom of is worse than any error message on it.
  return (
    <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
      <ScrollView
        contentContainerStyle={styles.container}
        keyboardShouldPersistTaps="handled"
        showsVerticalScrollIndicator={false}
      >
        <Image source={require('../../assets/icon.png')} style={styles.logo} resizeMode="contain" />
        <Text style={styles.title}>Create account</Text>
        <Banner>{error}</Banner>
        <Input
          style={styles.field}
          placeholder="Name"
          value={name}
          onChangeText={edit(setName, 'name')}
          invalid={!!problems.name}
        />
        <FieldError>{problems.name}</FieldError>
        <Input
          style={styles.field}
          placeholder="Handle (lowercase, no spaces)"
          autoCapitalize="none"
          autoCorrect={false}
          value={handle}
          onChangeText={edit(setHandle, 'handle')}
          invalid={!!problems.handle}
        />
        <FieldError>{problems.handle}</FieldError>
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
          placeholder="Password (min 8 characters)"
          secureTextEntry
          value={password}
          onChangeText={edit(setPassword, 'password')}
          invalid={!!problems.password}
        />
        <FieldError>{problems.password}</FieldError>
        <Input
          style={styles.field}
          placeholder="Confirm password"
          secureTextEntry
          value={confirmPassword}
          onChangeText={edit(setConfirmPassword, 'confirmPassword')}
          invalid={!!problems.confirmPassword}
        />
        <FieldError>{problems.confirmPassword}</FieldError>
        <SubmitButton style={styles.button} label="Sign up" busy={busy} onPress={submit} />
        <TouchableOpacity onPress={onGoToLogin}>
          <Text style={styles.link}>Have an account? Log in</Text>
        </TouchableOpacity>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  // flexGrow, not flex: centres a short form and grows past the screen for a
  // tall one.
  container: { flexGrow: 1, justifyContent: 'center', padding: 24 },
  logo: { width: 80, height: 80, borderRadius: 18, alignSelf: 'center', marginBottom: 12 },
  title: { fontSize: 28, fontWeight: '700', textAlign: 'center', marginBottom: 32, color: '#1f2937' },
  field: { marginBottom: 12 },
  button: { marginTop: 4 },
  link: { color: '#1f2937', textAlign: 'center', marginTop: 16, fontSize: 15 },
});
