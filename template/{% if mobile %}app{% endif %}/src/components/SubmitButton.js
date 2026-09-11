import { ActivityIndicator, StyleSheet, Text, TouchableOpacity } from 'react-native';

/**
 * The primary button on a form: label, busy spinner, disabled state. It does
 * not say what went wrong; failures go in the Banner at the top of the form.
 */
export default function SubmitButton({ label, busy, onPress, disabled, style }) {
  return (
    <TouchableOpacity
      style={[styles.button, disabled && styles.disabled, style]}
      onPress={onPress}
      disabled={disabled || busy}
      accessibilityRole="button"
      accessibilityLabel={label}
    >
      {busy ? <ActivityIndicator color="#fff" /> : <Text style={styles.text}>{label}</Text>}
    </TouchableOpacity>
  );
}

const styles = StyleSheet.create({
  button: {
    backgroundColor: '#1f2937',
    borderRadius: 8,
    padding: 14,
    alignItems: 'center',
    justifyContent: 'center',
    // Holds the height steady between the label and the spinner, so tapping
    // does not make the form jump.
    minHeight: 52,
  },
  disabled: { opacity: 0.45 },
  text: { color: '#fff', fontSize: 16, fontWeight: '600', textAlign: 'center' },
});
