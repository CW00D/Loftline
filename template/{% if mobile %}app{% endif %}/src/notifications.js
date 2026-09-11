// Push registration, best-effort in every direction.
//
// This file is part of the base app, not of the notifications overlay, and
// that is deliberate. The overlay lives in the API: it adds POST /me/push-token
// and the sender. A project without it answers 404 here, which is swallowed
// like every other failure, so the app is one artefact whether or not the
// project does push. Remote push also does not work in Expo Go, and the user
// may refuse permission. All of those are fine; try again next launch.
import * as Notifications from 'expo-notifications';
import Constants from 'expo-constants';
import { api } from './api';

// Show notifications when the app is in the foreground too.
Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowBanner: true,
    shouldShowList: true,
    shouldPlaySound: false,
    shouldSetBadge: false,
  }),
});

export async function registerForPush() {
  try {
    const perm = await Notifications.requestPermissionsAsync();
    if (!perm.granted) return;
    const projectId = Constants?.expoConfig?.extra?.eas?.projectId;
    if (!projectId) return; // not linked to an Expo project yet: nothing to register with
    const token = (await Notifications.getExpoPushTokenAsync({ projectId })).data;
    await api('/me/push-token', { method: 'POST', body: { token } });
  } catch {
    // See the note at the top.
  }
}
