import * as SecureStore from 'expo-secure-store';

const TOKEN_KEY = 'auth_token';

export function getToken() {
  return SecureStore.getItemAsync(TOKEN_KEY);
}

export function setToken(token) {
  return SecureStore.setItemAsync(TOKEN_KEY, token);
}

export function clearToken() {
  return SecureStore.deleteItemAsync(TOKEN_KEY);
}
