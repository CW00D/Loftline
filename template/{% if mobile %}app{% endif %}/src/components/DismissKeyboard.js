import { Keyboard, TouchableWithoutFeedback, View } from 'react-native';

/**
 * A View that puts the keyboard away when you tap the dead space on it.
 * Anything that handles its own touches still wins the responder, so this
 * only fires on the gaps between them. ScrollViews already do this themselves
 * via keyboardShouldPersistTaps; this is for screens without one.
 */
export default function DismissKeyboard({ style, children }) {
  return (
    <TouchableWithoutFeedback accessible={false} onPress={Keyboard.dismiss}>
      <View style={style}>{children}</View>
    </TouchableWithoutFeedback>
  );
}
