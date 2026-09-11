import { StyleSheet, Text, View } from 'react-native';

/**
 * The red card at the top of a form, holding whatever went wrong with the
 * attempt as a whole. Renders nothing when there is no message.
 *
 * A problem with one specific box belongs to that box: a red border and a
 * FieldError under it. This is for the failure of the whole attempt, a wrong
 * password, a taken handle, a server that is not there. Keep it short; it is
 * a headline, not an explanation.
 */
export default function Banner({ children, style }) {
  if (!children) return null;
  return (
    <View
      style={[styles.banner, style]}
      accessibilityRole="alert"
      accessibilityLiveRegion="polite"
    >
      <Text style={styles.icon}>!</Text>
      <Text style={styles.text}>{children}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  banner: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 10,
    backgroundColor: '#fef2f2',
    borderWidth: 1,
    borderColor: '#fecaca',
    borderRadius: 10,
    paddingVertical: 12,
    paddingHorizontal: 14,
    marginBottom: 16,
  },
  icon: { color: '#b91c1c', fontSize: 15, lineHeight: 21, fontWeight: '800' },
  text: { flex: 1, color: '#b91c1c', fontSize: 15, lineHeight: 21, fontWeight: '500' },
});
