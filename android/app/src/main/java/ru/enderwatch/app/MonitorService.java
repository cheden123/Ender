package ru.enderwatch.app;

import android.app.*;
import android.content.*;
import android.content.pm.ServiceInfo;
import android.os.*;
import java.util.concurrent.*;
import org.json.*;

public class MonitorService extends Service {
  static final String CHANNEL = "printer_monitor", ALERTS = "printer_alerts";
  private ScheduledExecutorService worker;
  public static boolean running = false;

  public void onCreate() {
    super.onCreate();
    NotificationManager n = getSystemService(NotificationManager.class);
    n.createNotificationChannel(
        new NotificationChannel(
            CHANNEL, "Наблюдение за принтером", NotificationManager.IMPORTANCE_LOW));
    n.createNotificationChannel(
        new NotificationChannel(
            ALERTS, "Обнаруженные проблемы печати", NotificationManager.IMPORTANCE_HIGH));
  }

  public int onStartCommand(Intent i, int flags, int id) {
    if (i != null && "stop".equals(i.getAction())) {
      stopSelf();
      return START_NOT_STICKY;
    }
    if (worker != null) return START_STICKY;
    running = true;
    Notification n =
        notice("Наблюдение включено", "Проверка событий принтера каждые 5 секунд", null)
            .setOngoing(true)
            .addAction(
                android.R.drawable.ic_menu_close_clear_cancel,
                "Выключить",
                PendingIntent.getService(
                    this,
                    9,
                    new Intent(this, MonitorService.class).setAction("stop"),
                    PendingIntent.FLAG_IMMUTABLE))
            .build();
    if (Build.VERSION.SDK_INT >= 34)
      startForeground(1, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE);
    else startForeground(1, n);
    worker = Executors.newSingleThreadScheduledExecutor();
    worker.scheduleWithFixedDelay(this::poll, 0, 5, TimeUnit.SECONDS);
    return START_STICKY;
  }

  private Notification.Builder notice(String title, String body, JSONObject alert) {
    Intent open =
        new Intent(this, AlertActivity.class)
            .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP);
    if (alert != null) open.putExtra("alert", alert.toString());
    return new Notification.Builder(this, alert == null ? CHANNEL : ALERTS)
        .setSmallIcon(android.R.drawable.ic_dialog_info)
        .setContentTitle(title)
        .setContentText(body)
        .setStyle(new Notification.BigTextStyle().bigText(body))
        .setAutoCancel(alert != null)
        .setContentIntent(
            PendingIntent.getActivity(
                this,
                alert == null ? 0 : alert.optInt("id"),
                open,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE));
  }

  private void poll() {
    try {
      Api api = new Api(this);
      android.content.SharedPreferences p = getSharedPreferences("ender", 0);
      String key = "cursor:" + api.base;
      long cursor = p.getLong(key, 0);
      JSONArray alerts = api.get("/api/alerts?after=" + cursor).optJSONArray("items");
      getSystemService(NotificationManager.class)
          .notify(
              1,
              notice("Наблюдение включено", "Сервер подключён · проверка каждые 5 секунд", null)
                  .setOngoing(true)
                  .addAction(
                      android.R.drawable.ic_menu_close_clear_cancel,
                      "Выключить",
                      PendingIntent.getService(
                          this,
                          9,
                          new Intent(this, MonitorService.class).setAction("stop"),
                          PendingIntent.FLAG_IMMUTABLE))
                  .build());
      if (alerts == null) return;
      for (int x = 0; x < alerts.length(); x++) {
        JSONObject a = alerts.getJSONObject(x);
        a.put("server", api.base);
        long aid = a.getLong("id");
        if (aid <= cursor) continue;
        Notification.Builder notice =
            notice("Проверьте печать", a.optString("message", "Обнаружена проблема"), a);
        try {
          String path = a.optString("snapshot", "");
          if (!path.isEmpty() && !path.equals("null")) {
            byte[] image = api.request(path, "GET", null, null);
            android.graphics.Bitmap bitmap = MainActivity.thumbnail(image, 800);
            if (bitmap != null)
              notice.setStyle(
                  new Notification.BigPictureStyle()
                      .bigPicture(bitmap)
                      .setSummaryText(a.optString("message")));
          }
        } catch (Exception ignored) {
        }
        getSystemService(NotificationManager.class)
            .notify((int) (aid % 2000000000) + 100, notice.build());
        cursor = aid;
        p.edit().putLong(key, cursor).apply();
      }
    } catch (Exception e) {
      getSystemService(NotificationManager.class)
          .notify(
              1,
              notice("Связь с сервером потеряна", "Повторное подключение… " + e.getMessage(), null)
                  .setOngoing(true)
                  .build());
    }
  }

  public void onDestroy() {
    running = false;
    if (worker != null) worker.shutdownNow();
    super.onDestroy();
  }

  public IBinder onBind(Intent i) {
    return null;
  }
}
