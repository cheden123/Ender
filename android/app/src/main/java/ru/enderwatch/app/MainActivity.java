package ru.enderwatch.app;

import android.Manifest;
import android.app.*;
import android.content.*;
import android.content.pm.PackageManager;
import android.database.Cursor;
import android.graphics.*;
import android.net.Uri;
import android.os.*;
import android.provider.OpenableColumns;
import android.view.*;
import android.widget.*;
import java.io.*;
import java.text.SimpleDateFormat;
import java.util.*;
import java.util.concurrent.*;
import org.json.*;

public class MainActivity extends Activity {
  final int BG = Color.rgb(13, 20, 29),
      CARD = Color.rgb(23, 34, 47),
      TEXT = Color.rgb(232, 240, 247),
      MUTED = Color.rgb(151, 170, 189),
      ACCENT = Color.rgb(75, 219, 183);
  LinearLayout root, content, nav;
  TextView connection, stats, temps, job, eta;
  ImageView camera;
  ProgressBar progress;
  Graph graph;
  String tab = "Обзор";
  JSONObject status;
  volatile long streamEpoch = 0;
  final Handler main = new Handler(Looper.getMainLooper());
  final ExecutorService io = Executors.newFixedThreadPool(3);
  volatile boolean visible = false, polling = false, streaming = false;
  volatile java.net.HttpURLConnection streamConnection;
  long page = 0, lastChart = 0, lastSuccess = 0;
  AlertDialog activeDialog;
  int historyOffset = 0;

  interface Task {
    void run() throws Exception;
  }

  public void onCreate(Bundle b) {
    super.onCreate(b);
    getWindow().setStatusBarColor(BG);
    getWindow().setNavigationBarColor(BG);
    root = new LinearLayout(this);
    root.setOrientation(1);
    root.setBackgroundColor(BG);
    root.setPadding(dp(16), dp(12), dp(16), 0);
    setContentView(root);
    LinearLayout head = row();
    TextView title = text("ENDER / WATCH", 23, TEXT);
    title.setTypeface(null, 1);
    head.addView(title, new LinearLayout.LayoutParams(0, -2, 1));
    Button settings = button("⚙", v -> settings());
    settings.setContentDescription("Настройки подключения к серверу");
    head.addView(settings);
    root.addView(head);
    connection =
        text(
            Api.configured(this) ? "Подключение…" : "Укажите адрес сервера и токен в настройках ⚙",
            12,
            MUTED);
    root.addView(connection);
    nav = row();
    for (String name : new String[] {"Обзор", "Файлы", "События", "История"}) {
      Button btab =
          button(
              name,
              v -> {
                historyOffset = 0;
                show(name);
              });
      btab.setTextSize(11);
      btab.setPadding(0, 0, 0, 0);
      btab.setMinWidth(0);
      btab.setSingleLine(true);
      nav.addView(btab, new LinearLayout.LayoutParams(0, dp(48), 1));
    }
    root.addView(nav);
    ScrollView scroll = new ScrollView(this);
    content = new LinearLayout(this);
    content.setOrientation(1);
    scroll.addView(content);
    root.addView(scroll, new LinearLayout.LayoutParams(-1, 0, 1));
    show("Обзор");
    handleIntent(getIntent());
    if (!Api.configured(this)) main.post(this::settings);
  }

  protected void onNewIntent(Intent i) {
    super.onNewIntent(i);
    setIntent(i);
    handleIntent(i);
  }

  void handleIntent(Intent i) {
    if (getClass() != AlertActivity.class) return;
    String a = i.getStringExtra("alert");
    if (a != null)
      try {
        main.post(
            () -> {
              try {
                alertDetail(new JSONObject(a));
              } catch (Exception e) {
                error(e);
              }
            });
      } catch (Exception e) {
        error(e);
      }
  }

  protected void onResume() {
    super.onResume();
    visible = true;
    main.post(tick);
  }

  protected void onPause() {
    visible = false;
    stopStream();
    if (activeDialog != null) activeDialog.dismiss();
    main.removeCallbacks(tick);
    super.onPause();
  }

  protected void onDestroy() {
    io.shutdownNow();
    super.onDestroy();
  }

  final Runnable tick =
      new Runnable() {
        public void run() {
          if (!visible) return;
          if (Api.configured(MainActivity.this) && !polling && tab.equals("Обзор")) {
            polling = true;
            long p = page;
            io.execute(
                () -> {
                  try {
                    Api api = new Api(MainActivity.this);
                    JSONObject s = api.get("/api/status");
                    JSONArray history = null;
                    if (System.currentTimeMillis() - lastChart > 10000) {
                      try {
                        history = api.get("/api/temperatures").optJSONArray("items");
                        lastChart = System.currentTimeMillis();
                      } catch (Exception ignored) {
                      }
                    }
                    JSONArray chart = history;
                    byte[] frame = null;
                    try {
                      if (!streaming) frame = api.request("/api/camera.jpg", "GET", null, null);
                    } catch (Exception ignored) {
                    }
                    byte[] f = frame;
                    main.post(
                        () -> {
                          if (p == page) {
                            status = s;
                            lastSuccess = System.currentTimeMillis();
                            renderStatus();
                            if (chart != null) {
                              graph.points = chart;
                              graph.invalidate();
                            }
                            if (f != null) {
                              Bitmap b = thumbnail(f, 1024);
                              if (b != null) camera.setImageBitmap(b);
                            } else if (!streaming) {
                              camera.setImageDrawable(null);
                              camera.setContentDescription("Камера недоступна");
                            }
                          }
                        });
                  } catch (Exception e) {
                    main.post(
                        () -> {
                          if (p == page) {
                            status = null;
                            connection.setTextColor(MUTED);
                            camera.setImageDrawable(null);
                            connection.setText(
                                e.getMessage() != null && e.getMessage().contains("HTTP 401")
                                    ? "Неверный токен · откройте настройки ⚙"
                                    : "Нет связи · проверьте адрес сервера и Wi-Fi");
                          }
                        });
                  } finally {
                    polling = false;
                  }
                });
          }
          main.postDelayed(this, 2000);
        }
      };

  void show(String name) {
    stopStream();
    tab = name;
    page++;
    content.removeAllViews();
    for (int n = 0; n < nav.getChildCount(); n++) {
      Button b = (Button) nav.getChildAt(n);
      b.setTextColor(b.getText().equals(name) ? ACCENT : MUTED);
    }
    if (name.equals("Обзор")) dashboard();
    else if (name.equals("Файлы")) files();
    else if (name.equals("События")) alerts();
    else history();
  }

  void dashboard() {
    camera = new ImageView(this);
    camera.setBackgroundColor(CARD);
    camera.setScaleType(ImageView.ScaleType.FIT_CENTER);
    content.addView(camera, new LinearLayout.LayoutParams(-1, dp(210)));
    content.addView(button("▶ Прямой эфир", v -> live()));
    LinearLayout box = card();
    job = text("Ожидание данных", 19, TEXT);
    box.addView(job);
    stats = text("—", 13, ACCENT);
    box.addView(stats);
    progress = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
    progress.setMax(1000);
    box.addView(progress, new LinearLayout.LayoutParams(-1, dp(14)));
    eta = text("—", 13, MUTED);
    box.addView(eta);
    temps = text("Сопло —     Стол —", 16, TEXT);
    box.addView(temps);
    graph = new Graph();
    box.addView(graph, new LinearLayout.LayoutParams(-1, dp(110)));
    LinearLayout controls = row();
    controls.addView(button("Пауза", v -> control("pause")), weight());
    controls.addView(button("Продолжить", v -> control("resume")), weight());
    controls.addView(button("Отмена", v -> control("cancel")), weight());
    box.addView(controls);
    content.addView(button("Уведомления в фоне", v -> monitor()));
    if (status != null) renderStatus();
    long p = page;
    work(
        () -> {
          JSONArray points = new Api(this).get("/api/temperatures").optJSONArray("items");
          main.post(
              () -> {
                if (p == page) {
                  graph.points = points;
                  graph.invalidate();
                }
              });
        });
  }

  void renderStatus() {
    boolean connected = status.optBoolean("connected");
    connection.setText(connected ? "● Принтер подключён" : "○ Принтер не подключён");
    connection.setTextColor(connected ? ACCENT : MUTED);
    job.setText(
        status.optString("filename", "").isEmpty()
            ? "Готов к печати"
            : status.optString("filename"));
    String state = status.optString("state");
    stats.setText(
        stateName(state) + " · Детектор: " + detectorName(status.optString("detector", "—")));
    double p = status.optDouble("progress", 0);
    progress.setProgress((int) (p * 1000));
    eta.setText(
        String.format(Locale.US, "%.1f%%", p * 100)
            + "   ·   Осталось "
            + duration(status.optLong("eta_seconds", 0)));
    temps.setText(
        String.format(
            Locale.US,
            "Сопло %.0f / %.0f°   ·   Стол %.0f / %.0f°",
            status.optDouble("nozzle"),
            status.optDouble("nozzle_target"),
            status.optDouble("bed"),
            status.optDouble("bed_target")));
  }

  void control(String action) {
    if (status == null
        || System.currentTimeMillis() - lastSuccess > 10000
        || !status.optBoolean("connected")
        || status.isNull("job_id")) {
      toast("Нет активного задания");
      return;
    }
    String state = status.optString("state");
    if ((action.equals("pause") && !state.equals("printing"))
        || (action.equals("resume") && !state.equals("paused"))) {
      toast("Команда недоступна в текущем состоянии");
      return;
    }
    long id = status.optLong("job_id");
    String server = new Api(this).base;
    confirm(
        "Управление печатью",
        action.equals("cancel")
            ? "Отменить текущее задание?"
            : "Выполнить «" + (action.equals("pause") ? "Пауза" : "Продолжить") + "»?",
        () -> sendControl(action, id, server));
  }

  void sendControl(String action, long id, String server) {
    work(
        () -> {
          Api api = new Api(this);
          if (!api.base.equals(server))
            throw new IOException("Сервер изменился. Откройте событие заново.");
          api.post(action, new JSONObject().put("job_id", id));
          main.post(() -> toast("Команда отправлена"));
        });
  }

  void files() {
    content.addView(
        button(
            "＋ Загрузить G-code",
            v -> {
              Intent i =
                  new Intent(Intent.ACTION_OPEN_DOCUMENT)
                      .setType("*/*")
                      .addCategory(Intent.CATEGORY_OPENABLE);
              startActivityForResult(i, 41);
            }));
    TextView loading = text("Загрузка файлов…", 14, MUTED);
    content.addView(loading);
    long p = page;
    work(
        () -> {
          JSONArray items = new Api(this).get("/api/files").getJSONArray("items");
          main.post(
              () -> {
                if (p != page) return;
                content.removeView(loading);
                if (items.length() == 0)
                  content.addView(text("Файлов пока нет. Добавьте .gcode с телефона.", 15, MUTED));
                for (int n = 0; n < items.length(); n++) {
                  JSONObject f = items.optJSONObject(n);
                  if (f == null) continue;
                  String name = f.optString("name");
                  LinearLayout c = card();
                  c.addView(text(name, 16, TEXT));
                  c.addView(
                      text(
                          String.format(Locale.US, "%.1f МБ", f.optLong("size") / 1048576.0),
                          12,
                          MUTED));
                  c.addView(
                      button(
                          "Начать печать",
                          v ->
                              confirm(
                                  "Запуск печати",
                                  "Напечатать «" + name + "»? Убедитесь, что стол свободен.",
                                  () ->
                                      work(
                                          () -> {
                                            new Api(this)
                                                .post(
                                                    "start",
                                                    new JSONObject().put("filename", name));
                                            main.post(
                                                () -> {
                                                  toast("Печать запущена");
                                                  show("Обзор");
                                                });
                                          }))));
                }
              });
        });
  }

  protected void onActivityResult(int request, int result, Intent data) {
    super.onActivityResult(request, result, data);
    if (request != 41 || result != RESULT_OK || data == null) return;
    Uri uri = data.getData();
    if (uri == null) return;
    String name = "upload.gcode";
    try (Cursor c = getContentResolver().query(uri, null, null, null, null)) {
      if (c != null && c.moveToFirst()) {
        int index = c.getColumnIndex(OpenableColumns.DISPLAY_NAME);
        if (index >= 0) name = c.getString(index);
      }
    }
    if (!name.toLowerCase(Locale.ROOT).endsWith(".gcode")) {
      toast("Выберите файл .gcode");
      return;
    }
    String filename = name;
    toast("Загрузка «" + name + "»…");
    work(
        () -> {
          try (InputStream in = getContentResolver().openInputStream(uri)) {
            new Api(this).upload(filename, in);
          }
          main.post(
              () -> {
                toast("Файл загружен");
                show("Файлы");
              });
        });
  }

  void alerts() {
    content.addView(
        text(
            "Снимки сохраняют момент обнаружения. Пауза всегда требует вашего подтверждения.",
            14,
            MUTED));
    long p = page;
    work(
        () -> {
          JSONArray items = new Api(this).get("/api/alerts").getJSONArray("items");
          main.post(
              () -> {
                if (p != page) return;
                if (items.length() == 0) content.addView(text("Проблем не обнаружено", 18, ACCENT));
                for (int n = 0; n < items.length(); n++) {
                  JSONObject a = items.optJSONObject(n);
                  if (a == null) continue;
                  try {
                    a.put("server", new Api(this).base);
                  } catch (Exception ignored) {
                  }
                  LinearLayout c = card();
                  c.addView(text(date(a.optLong("ts")), 12, MUTED));
                  c.addView(text(a.optString("message"), 17, TEXT));
                  c.addView(button("Снимок и действия", v -> alertDetail(a)));
                }
              });
        });
  }

  void alertDetail(JSONObject a) {
    String server = a.optString("server", new Api(this).base);
    if (!server.equals(new Api(this).base)) {
      toast("Событие относится к другому серверу");
      return;
    }
    LinearLayout body = new LinearLayout(this);
    body.setPadding(dp(18), dp(12), dp(18), dp(12));
    body.setOrientation(1);
    body.setBackgroundColor(CARD);
    body.addView(text(date(a.optLong("ts")) + " · задание #" + a.optLong("job_id"), 13, MUTED));
    body.addView(text(a.optString("message"), 17, TEXT));
    ImageView image = new ImageView(this);
    image.setAdjustViewBounds(true);
    body.addView(image, new LinearLayout.LayoutParams(-1, dp(240)));
    loadImage(a.optString("snapshot", null), image);
    body.addView(
        text(
            "Это сохранённый снимок события. Команда относится только к исходному заданию; сервер"
                + " отклонит её, если печать уже сменилась.",
            13,
            MUTED));
    AlertDialog dialog =
        new AlertDialog.Builder(this)
            .setTitle("Проверьте печать")
            .setView(body)
            .setNegativeButton("Закрыть", null)
            .setPositiveButton(
                "Приостановить",
                (d, w) ->
                    confirm(
                        "Ручная пауза",
                        "Приостановить задание #" + a.optLong("job_id") + "?",
                        () -> sendControl("pause", a.optLong("job_id"), server)))
            .create();
    dialog.show();
  }

  void history() {
    long p = page;
    work(
        () -> {
          JSONArray items = new Api(this).get("/api/history").getJSONArray("items");
          main.post(
              () -> {
                if (page != p) return;
                if (items.length() == 0)
                  content.addView(text("Здесь появятся завершённые задания", 17, MUTED));
                for (int n = historyOffset; n < Math.min(items.length(), historyOffset + 8); n++) {
                  JSONObject h = items.optJSONObject(n);
                  if (h == null) continue;
                  LinearLayout c = card();
                  c.addView(text(h.optString("filename"), 17, TEXT));
                  c.addView(
                      text(
                          stateName(h.optString("state")) + " · " + date(h.optLong("started")),
                          13,
                          MUTED));
                  if (!h.isNull("snapshot")) {
                    ImageView image = new ImageView(this);
                    image.setScaleType(ImageView.ScaleType.FIT_CENTER);
                    c.addView(image, new LinearLayout.LayoutParams(-1, dp(160)));
                    loadImage(h.optString("snapshot"), image);
                  }
                  if (!h.isNull("timelapse") && !h.optString("timelapse").isEmpty())
                    c.addView(button("▶ Таймлапс", v -> video(h.optString("timelapse"))));
                }
                if (historyOffset > 0)
                  content.addView(
                      button(
                          "← Предыдущие задания",
                          v -> {
                            historyOffset = Math.max(0, historyOffset - 8);
                            show("История");
                          }));
                if (historyOffset + 8 < items.length())
                  content.addView(
                      button(
                          "Следующие задания →",
                          v -> {
                            historyOffset += 8;
                            show("История");
                          }));
              });
        });
  }

  void stopStream() {
    streamEpoch++;
    streaming = false;
    java.net.HttpURLConnection c = streamConnection;
    streamConnection = null;
    if (c != null) c.disconnect();
  }

  void live() {
    stopStream();
    ImageView view = new ImageView(this);
    view.setScaleType(ImageView.ScaleType.FIT_CENTER);
    view.setBackgroundColor(BG);
    activeDialog =
        new AlertDialog.Builder(this)
            .setTitle("Прямой эфир")
            .setView(view)
            .setNegativeButton("Закрыть", null)
            .create();
    activeDialog.setOnDismissListener(d -> stopStream());
    activeDialog.show();
    activeDialog.getWindow().setLayout(-1, -1);
    streaming = true;
    long epoch = streamEpoch;
    io.execute(
        () -> {
          java.net.HttpURLConnection own = null;
          try {
            java.net.HttpURLConnection c = new Api(this).connection("/api/camera.mjpg");
            own = c;
            streamConnection = c;
            c.setReadTimeout(10000);
            if (c.getResponseCode() != 200) throw new IOException("Камера недоступна");
            try (InputStream in = new BufferedInputStream(c.getInputStream(), 65536)) {
              ByteArrayOutputStream jpeg = new ByteArrayOutputStream();
              int previous = -1, current;
              boolean frame = false;
              while (epoch == streamEpoch && streaming && visible && (current = in.read()) != -1) {
                if (!frame && previous == 255 && current == 216) {
                  frame = true;
                  jpeg.reset();
                  jpeg.write(255);
                }
                if (frame) {
                  jpeg.write(current);
                  if (jpeg.size() > 8 * 1024 * 1024) throw new IOException("Слишком большой кадр");
                  if (previous == 255 && current == 217) {
                    byte[] bytes = jpeg.toByteArray();
                    Bitmap bitmap = thumbnail(bytes, 1600);
                    if (bitmap != null)
                      main.post(
                          () -> {
                            if (epoch == streamEpoch && streaming) view.setImageBitmap(bitmap);
                          });
                    frame = false;
                  }
                }
                previous = current;
              }
            }
          } catch (Exception e) {
            if (epoch == streamEpoch && streaming)
              main.post(
                  () -> {
                    toast("Прямой эфир недоступен. Показываем снимки.");
                    if (activeDialog != null) activeDialog.dismiss();
                  });
          } finally {
            if (own != null) own.disconnect();
            if (epoch == streamEpoch) {
              streaming = false;
              streamConnection = null;
            }
          }
        });
  }

  void video(String path) {
    try {
      Api api = new Api(this);
      VideoView video = new VideoView(this);
      HashMap<String, String> headers = new HashMap<>();
      headers.put("Authorization", "Bearer " + api.token);
      video.setVideoURI(Uri.parse(api.originPath(path)), headers);
      MediaController media = new MediaController(this);
      media.setAnchorView(video);
      video.setMediaController(media);
      Dialog dialog = new Dialog(this);
      dialog.setContentView(video);
      dialog.getWindow().setLayout(-1, dp(330));
      video.setOnPreparedListener(mp -> video.start());
      video.setOnErrorListener(
          (mp, a, b) -> {
            toast("Не удалось воспроизвести видео");
            return true;
          });
      dialog.setOnDismissListener(d -> video.stopPlayback());
      dialog.show();
      dialog.getWindow().setLayout(-1, dp(330));
    } catch (Exception e) {
      error(e);
    }
  }

  void loadImage(String path, ImageView image) {
    if (path == null || path.isEmpty() || path.equals("null")) return;
    long expected = page;
    work(
        () -> {
          if (page != expected) return;
          byte[] bytes = new Api(this).request(path, "GET", null, null);
          Bitmap bitmap = thumbnail(bytes, 640);
          main.post(
              () -> {
                if (page == expected) image.setImageBitmap(bitmap);
              });
        });
  }

  static Bitmap thumbnail(byte[] bytes, int maxWidth) {
    BitmapFactory.Options bounds = new BitmapFactory.Options();
    bounds.inJustDecodeBounds = true;
    BitmapFactory.decodeByteArray(bytes, 0, bytes.length, bounds);
    BitmapFactory.Options opts = new BitmapFactory.Options();
    opts.inSampleSize = 1;
    while (bounds.outWidth / opts.inSampleSize > maxWidth
        || bounds.outHeight / opts.inSampleSize > maxWidth) opts.inSampleSize *= 2;
    return BitmapFactory.decodeByteArray(bytes, 0, bytes.length, opts);
  }

  void settings() {
    LinearLayout fields = new LinearLayout(this);
    fields.setPadding(dp(20), 0, dp(20), 0);
    fields.setOrientation(1);
    Api api = new Api(this);
    fields.addView(
        text(
            "Запустите Ender Watch Server на компьютере рядом с принтером. Подключите телефон к той"
                + " же сети Wi-Fi. Адрес и токен указаны в настройках сервера.",
            14,
            TEXT));
    fields.addView(text("Адрес сервера", 13, MUTED));
    EditText url = new EditText(this);
    url.setSingleLine(true);
    url.setHint("http://192.168.1.100:8080");
    url.setText(api.base);
    url.setInputType(17);
    fields.addView(url);
    fields.addView(text("Токен доступа (APP_TOKEN)", 13, MUTED));
    EditText token = new EditText(this);
    token.setSingleLine(true);
    token.setHint("Токен сервера");
    token.setInputType(129);
    token.setText(api.token);
    fields.addView(token);
    TextView hint =
        text(
            "Для домашней сети укажите адрес сервера. Вне дома используйте HTTPS или VPN.",
            13,
            MUTED);
    fields.addView(hint);
    AlertDialog d =
        new AlertDialog.Builder(this)
            .setTitle("Подключение")
            .setView(fields)
            .setPositiveButton("Сохранить", null)
            .setNegativeButton("Отмена", null)
            .create();
    d.setOnShowListener(
        x ->
            d.getButton(-1)
                .setOnClickListener(
                    v -> {
                      try {
                        Uri u = Uri.parse(url.getText().toString().trim());
                        if (!("http".equals(u.getScheme()) || "https".equals(u.getScheme()))
                            || u.getHost() == null
                            || u.getUserInfo() != null
                            || u.getQuery() != null
                            || u.getFragment() != null
                            || !(u.getPath() == null
                                || u.getPath().isEmpty()
                                || u.getPath().equals("/")))
                          throw new Exception("Нужен адрес вида http://сервер:8080 без пути");
                        if (token.getText().toString().trim().isEmpty())
                          throw new Exception("Введите токен сервера");
                        Secrets.save(this, token.getText().toString().trim());
                        stopService(new Intent(this, MonitorService.class));
                        getSharedPreferences("ender", 0)
                            .edit()
                            .putString("url", url.getText().toString().trim())
                            .apply();
                        status = null;
                        d.dismiss();
                        show(tab);
                      } catch (Exception e) {
                        url.setError(e.getMessage());
                      }
                    }));
    d.show();
  }

  void monitor() {
    if (!Api.configured(this)) {
      settings();
      return;
    }
    if (MonitorService.running) {
      new AlertDialog.Builder(this)
          .setTitle("Фоновое наблюдение включено")
          .setMessage("Остановить проверку событий и уведомления?")
          .setPositiveButton(
              "Остановить",
              (d, w) -> {
                stopService(new Intent(this, MonitorService.class));
                toast("Наблюдение выключено");
              })
          .setNegativeButton("Оставить", null)
          .show();
      return;
    }
    if (Build.VERSION.SDK_INT >= 33
        && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)
            != PackageManager.PERMISSION_GRANTED) {
      requestPermissions(new String[] {Manifest.permission.POST_NOTIFICATIONS}, 52);
      return;
    }
    startForegroundService(new Intent(this, MonitorService.class));
    toast("Фоновое наблюдение включено");
  }

  public void onRequestPermissionsResult(int r, String[] p, int[] g) {
    super.onRequestPermissionsResult(r, p, g);
    if (r == 52 && g.length > 0 && g[0] == PackageManager.PERMISSION_GRANTED) monitor();
    else if (r == 52) toast("Для оповещений разрешите уведомления в настройках Android");
  }

  void work(Task t) {
    if (!Api.configured(this)) return;
    io.execute(
        () -> {
          try {
            t.run();
          } catch (Exception e) {
            main.post(() -> error(e));
          }
        });
  }

  void confirm(String title, String message, Runnable yes) {
    new AlertDialog.Builder(this)
        .setTitle(title)
        .setMessage(message)
        .setNegativeButton("Назад", null)
        .setPositiveButton("Подтвердить", (d, w) -> yes.run())
        .show();
  }

  void error(Exception e) {
    toast(e.getMessage() == null ? "Ошибка связи" : e.getMessage());
  }

  void toast(String s) {
    Toast.makeText(this, s, Toast.LENGTH_LONG).show();
  }

  int dp(int x) {
    return (int) (getResources().getDisplayMetrics().density * x + .5f);
  }

  LinearLayout row() {
    LinearLayout l = new LinearLayout(this);
    l.setGravity(Gravity.CENTER_VERTICAL);
    return l;
  }

  LinearLayout.LayoutParams weight() {
    return new LinearLayout.LayoutParams(0, dp(48), 1);
  }

  TextView text(String s, int size, int color) {
    TextView t = new TextView(this);
    t.setText(s);
    t.setTextSize(size);
    t.setTextColor(color);
    t.setPadding(0, dp(6), 0, dp(6));
    return t;
  }

  Button button(String label, View.OnClickListener click) {
    Button b = new Button(this);
    b.setText(label);
    b.setTextSize(13);
    b.setAllCaps(false);
    b.setTextColor(ACCENT);
    b.setBackgroundTintList(android.content.res.ColorStateList.valueOf(CARD));
    b.setOnClickListener(click);
    return b;
  }

  LinearLayout card() {
    LinearLayout c = new LinearLayout(this);
    c.setOrientation(1);
    c.setPadding(dp(14), dp(10), dp(14), dp(12));
    android.graphics.drawable.GradientDrawable bg =
        new android.graphics.drawable.GradientDrawable();
    bg.setColor(CARD);
    bg.setCornerRadius(dp(16));
    c.setBackground(bg);
    LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-1, -2);
    lp.setMargins(0, dp(12), 0, dp(4));
    content.addView(c, lp);
    return c;
  }

  String date(long seconds) {
    return seconds <= 0
        ? "—"
        : new SimpleDateFormat("dd.MM HH:mm", Locale.getDefault()).format(new Date(seconds * 1000));
  }

  String duration(long s) {
    return s <= 0 ? "—" : String.format(Locale.US, "%d ч %02d мин", s / 3600, (s % 3600) / 60);
  }

  String detectorName(String s) {
    switch (s) {
      case "ready":
      case "active":
        return "включён";
      case "disabled":
        return "выключен";
      case "warming_up":
        return "подготовка";
      case "unavailable":
        return "недоступен";
      case "monitoring":
        return "наблюдение";
      default:
        return s;
    }
  }

  String stateName(String s) {
    switch (s.toLowerCase(Locale.ROOT)) {
      case "printing":
        return "Печать";
      case "paused":
        return "Пауза";
      case "idle":
      case "standby":
        return "Ожидание";
      case "completed":
      case "complete":
      case "done":
        return "Завершено";
      case "cancelled":
      case "canceled":
        return "Отменено";
      case "error":
        return "Ошибка";
      case "interrupted":
        return "Прервано";
      case "offline":
        return "Нет связи";
      default:
        return s;
    }
  }

  final class Graph extends View {
    JSONArray points;
    Paint paint = new Paint(3);

    Graph() {
      super(MainActivity.this);
    }

    protected void onDraw(Canvas c) {
      super.onDraw(c);
      paint.setColor(MUTED);
      paint.setTextSize(dp(10));
      c.drawText("ТЕМПЕРАТУРА   ·   сопло / стол", 0, dp(15), paint);
      if (points == null || points.length() < 2) {
        c.drawText("История температуры появится после подключения", 0, dp(55), paint);
        return;
      }
      for (String field : new String[] {"nozzle", "bed"}) {
        paint.setColor(field.equals("nozzle") ? ACCENT : Color.rgb(255, 183, 94));
        paint.setStrokeWidth(dp(2));
        paint.setStyle(Paint.Style.STROKE);
        Path path = new Path();
        for (int n = 0; n < points.length(); n++) {
          JSONObject p = points.optJSONObject(n);
          float x = n * (getWidth() - 4f) / (points.length() - 1);
          float y =
              getHeight()
                  - dp(8)
                  - (float) (p == null ? 0 : p.optDouble(field, 0)) / 300f * (getHeight() - dp(30));
          if (n == 0) path.moveTo(x, y);
          else path.lineTo(x, y);
        }
        c.drawPath(path, paint);
        paint.setStyle(Paint.Style.FILL);
      }
    }
  }
}
