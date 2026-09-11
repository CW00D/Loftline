import { useState } from 'react';
import { Platform, StyleSheet, Text, TextInput } from 'react-native';

/**
 * The app's text field and the red line that sits below it when it is wrong.
 *
 * `focused` is tracked internally: the accent border says which field the
 * keyboard is pointed at. `invalid` is passed in and colours the box red. It
 * is only ever half the signal: pair it with a <FieldError> saying what is
 * actually wrong, because colour alone is invisible to a good number of
 * people and says nothing about what to do next.
 *
 * The unfocused box is untouched by either state, so switching states cannot
 * reflow the form by a pixel. The ring is drawn with boxShadow, which sits
 * outside the layout box (RN 0.76+, New Architecture).
 */

const ACCENT = '#1f2937';
const DANGER = '#dc2626';

export function Input({ style, placeholderTextColor, invalid, onFocus, onBlur, ...props }) {
  const [focused, setFocused] = useState(false);
  return (
    <TextInput
      {...props}
      // Chained, never replaced: a caller may want its own handler, and
      // silently dropping it would be a maddening thing to debug.
      onFocus={(e) => {
        setFocused(true);
        onFocus?.(e);
      }}
      onBlur={(e) => {
        setFocused(false);
        onBlur?.(e);
      }}
      placeholderTextColor={placeholderTextColor || '#aaa'}
      style={[
        styles.input,
        focused && styles.focused,
        // After focused, so a field that is both reads as wrong rather than
        // as merely current.
        invalid && styles.invalid,
        props.secureTextEntry && styles.secure,
        style,
      ]}
    />
  );
}

/**
 * The sentence under an invalid field. Renders nothing unless given an actual
 * sentence.
 */
export function FieldError({ children, style }) {
  if (typeof children !== 'string' || !children) return null;
  return <Text style={[styles.fieldError, style]}>{children}</Text>;
}

const styles = StyleSheet.create({
  // letterSpacing pinned to 0: iOS renders a bare single-line TextInput's
  // placeholder with loose kerning when it is unset.
  input: {
    borderRadius: 10,
    padding: 12,
    fontSize: 16,
    letterSpacing: 0,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: '#ccc',
  },
  focused: {
    borderWidth: 1,
    borderColor: ACCENT,
    boxShadow: `0 0 0 3px ${ACCENT}2E`,
  },
  invalid: {
    borderWidth: 1,
    borderColor: DANGER,
    boxShadow: `0 0 0 3px ${DANGER}29`,
  },
  // Android runs a secureTextEntry field's hint through the password
  // transformation, so an empty password box shows its placeholder in a wide
  // monospace. Naming the default family keeps it in Roboto.
  secure: Platform.OS === 'android' ? { fontFamily: 'sans-serif' } : {},
  // Tight against its field, so it reads as belonging to that box rather than
  // to the form. Negative margin because fields space with marginBottom.
  fieldError: {
    color: DANGER,
    fontSize: 14,
    fontWeight: '500',
    marginTop: -4,
    marginBottom: 10,
    marginLeft: 2,
  },
});
