package ru.enderwatch.app;

import android.content.Context;
import android.security.keystore.*;
import android.util.Base64;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import javax.crypto.*;
import javax.crypto.spec.GCMParameterSpec;

final class Secrets {
  static javax.crypto.SecretKey key() throws Exception {
    KeyStore k = KeyStore.getInstance("AndroidKeyStore");
    k.load(null);
    if (!k.containsAlias("ender_token")) {
      KeyGenerator g = KeyGenerator.getInstance("AES", "AndroidKeyStore");
      g.init(
          new KeyGenParameterSpec.Builder(
                  "ender_token", KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
              .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
              .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
              .build());
      g.generateKey();
    }
    return (javax.crypto.SecretKey) k.getKey("ender_token", null);
  }

  static String read(Context context) {
    String stored = context.getSharedPreferences("ender", 0).getString("token_enc", "");
    if (stored.isEmpty()) return "";
    try {
      String[] parts = stored.split(":");
      Cipher c = Cipher.getInstance("AES/GCM/NoPadding");
      c.init(Cipher.DECRYPT_MODE, key(), new GCMParameterSpec(128, Base64.decode(parts[0], 0)));
      return new String(c.doFinal(Base64.decode(parts[1], 0)), StandardCharsets.UTF_8);
    } catch (Exception e) {
      return "";
    }
  }

  static void save(Context context, String token) throws Exception {
    Cipher c = Cipher.getInstance("AES/GCM/NoPadding");
    c.init(Cipher.ENCRYPT_MODE, key());
    String encrypted =
        Base64.encodeToString(c.getIV(), 2)
            + ":"
            + Base64.encodeToString(c.doFinal(token.getBytes(StandardCharsets.UTF_8)), 2);
    context
        .getSharedPreferences("ender", 0)
        .edit()
        .putString("token_enc", encrypted)
        .remove("token")
        .apply();
  }
}
