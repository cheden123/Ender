package ru.enderwatch.app;

import android.content.Context;
import android.content.SharedPreferences;
import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import org.json.*;

final class Api {
  static boolean configured(Context c) {
    return !c.getSharedPreferences("ender", 0).getString("url", "").isEmpty();
  }

  final String base, token;

  Api(Context c) {
    SharedPreferences p = c.getSharedPreferences("ender", 0);
    base = p.getString("url", "").replaceAll("/+$", "");
    token = Secrets.read(c);
  }

  String originPath(String path) throws IOException {
    if (path == null || !path.startsWith("/") || path.startsWith("//"))
      throw new IOException("Неверный путь сервера");
    return base + path;
  }

  HttpURLConnection connection(String path) throws IOException {
    HttpURLConnection c = (HttpURLConnection) new URL(originPath(path)).openConnection();
    c.setConnectTimeout(7000);
    c.setReadTimeout(10000);
    c.setInstanceFollowRedirects(false);
    c.setRequestProperty("Authorization", "Bearer " + token);
    return c;
  }

  byte[] request(String path, String method, byte[] data, String type) throws Exception {
    HttpURLConnection c = connection(path);
    try {
      c.setRequestMethod(method);
      if (data != null) {
        c.setDoOutput(true);
        c.setRequestProperty("Content-Type", type);
        c.getOutputStream().write(data);
      }
      int code = c.getResponseCode();
      InputStream s = code >= 400 ? c.getErrorStream() : c.getInputStream();
      byte[] result = read(s);
      if (code < 200 || code >= 300) {
        String detail = new String(result, StandardCharsets.UTF_8);
        try {
          detail = new JSONObject(detail).optString("detail", detail);
        } catch (Exception ignored) {
        }
        throw new IOException("HTTP " + code + ": " + detail);
      }
      return result;
    } finally {
      c.disconnect();
    }
  }

  JSONObject get(String path) throws Exception {
    return new JSONObject(new String(request(path, "GET", null, null), StandardCharsets.UTF_8));
  }

  void post(String action, JSONObject body) throws Exception {
    request(
        "/api/control/" + action,
        "POST",
        body.toString().getBytes(StandardCharsets.UTF_8),
        "application/json");
  }

  void upload(String name, InputStream in) throws Exception {
    HttpURLConnection c = connection("/api/upload?name=" + URLEncoder.encode(name, "UTF-8"));
    try {
      c.setRequestMethod("POST");
      c.setDoOutput(true);
      c.setChunkedStreamingMode(65536);
      c.setRequestProperty("Content-Type", "application/octet-stream");
      try (OutputStream out = c.getOutputStream()) {
        byte[] b = new byte[65536];
        int n;
        while ((n = in.read(b)) != -1) out.write(b, 0, n);
      }
      int code = c.getResponseCode();
      if (code < 200 || code >= 300)
        throw new IOException(
            "Загрузка: HTTP "
                + code
                + " "
                + new String(read(c.getErrorStream()), StandardCharsets.UTF_8));
    } finally {
      c.disconnect();
    }
  }

  static byte[] read(InputStream in) throws IOException {
    if (in == null) return new byte[0];
    try (InputStream s = in;
        ByteArrayOutputStream out = new ByteArrayOutputStream()) {
      byte[] b = new byte[8192];
      int n;
      while ((n = s.read(b)) != -1) {
        if (out.size() + n > 12 * 1024 * 1024)
          throw new IOException("Ответ сервера превышает 12 МБ");
        out.write(b, 0, n);
      }
      return out.toByteArray();
    }
  }
}
