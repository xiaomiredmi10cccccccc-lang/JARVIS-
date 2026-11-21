package org.jarvis.jarvis;

import android.app.Notification;
import android.content.Context;
import android.media.AudioAttributes;
import android.media.AudioDeviceInfo;
import android.media.AudioManager;
import android.media.MediaPlayer;
import android.net.ConnectivityManager;
import android.net.NetworkInfo;
import android.os.Bundle;
import android.os.PowerManager;
import android.service.notification.NotificationListenerService;
import android.service.notification.StatusBarNotification;
import android.speech.tts.TextToSpeech;
import android.speech.tts.UtteranceProgressListener;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.OutputStreamWriter;
import java.io.Writer;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.Locale;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

/**
 * Collects the text of incoming message notifications (WhatsApp, Telegram, SMS, e-mail ...) into files/incoming.jsonl and,
 * when the user allowed it (files/reader_cfg.json, mode "always" or "headphones"), reads each one aloud even when the app is closed:
 * with the S.A.I voice from the server when the internet works, otherwise with the phone's own voice.
 * Nothing is collected unless the file files/reader_on exists (the switch in the app's settings creates and deletes it).
 */
public class NotifyListener extends NotificationListenerService {
    private static String lastKey = "";
    private static final ExecutorService VOICE = Executors.newSingleThreadExecutor();

    private static boolean wanted(String pkg, String category) {
        if (Notification.CATEGORY_MESSAGE.equals(category) || Notification.CATEGORY_EMAIL.equals(category)
                || Notification.CATEGORY_SOCIAL.equals(category)) {
            return true;
        }
        return pkg.equals("com.whatsapp") || pkg.equals("com.whatsapp.w4b") || pkg.equals("org.telegram.messenger")
                || pkg.equals("org.telegram.messenger.web") || pkg.equals("com.viber.voip") || pkg.equals("com.google.android.apps.messaging")
                || pkg.equals("com.android.mms") || pkg.equals("com.samsung.android.messaging") || pkg.equals("com.instagram.android")
                || pkg.equals("com.facebook.orca") || pkg.equals("com.google.android.gm");
    }

    private static String appName(String pkg) {
        if (pkg.startsWith("com.whatsapp")) return "WhatsApp";
        if (pkg.startsWith("org.telegram")) return "Telegram";
        if (pkg.equals("com.viber.voip")) return "Viber";
        if (pkg.equals("com.instagram.android")) return "Instagram";
        if (pkg.equals("com.facebook.orca")) return "Messenger";
        if (pkg.equals("com.google.android.gm")) return "Gmail";
        if (pkg.contains("messaging") || pkg.contains("mms")) return "SMS";
        String last = pkg.substring(pkg.lastIndexOf('.') + 1);
        return last.length() > 0 ? Character.toUpperCase(last.charAt(0)) + last.substring(1) : pkg;
    }

    @Override
    public void onNotificationPosted(StatusBarNotification sbn) {
        try {
            File dir = getFilesDir();
            if (!new File(dir, "reader_on").exists()) return;
            String pkg = sbn.getPackageName();
            if (pkg == null || pkg.equals(getPackageName()) || pkg.equals("android") || pkg.startsWith("com.android.systemui")) return;
            Notification n = sbn.getNotification();
            if (n == null) return;
            if ((n.flags & Notification.FLAG_GROUP_SUMMARY) != 0 || (n.flags & Notification.FLAG_ONGOING_EVENT) != 0) return;
            if (!wanted(pkg, n.category)) return;
            Bundle e = n.extras;
            if (e == null) return;
            CharSequence title = e.getCharSequence(Notification.EXTRA_TITLE);
            CharSequence text = e.getCharSequence(Notification.EXTRA_BIG_TEXT);
            if (text == null) text = e.getCharSequence(Notification.EXTRA_TEXT);
            if (text == null || text.toString().trim().length() == 0) return;
            final String t = title == null ? "" : title.toString();
            String b = text.toString();
            if (b.length() > 600) b = b.substring(0, 600);
            final String body = b;
            String key = pkg + "|" + t + "|" + body;
            long ts = System.currentTimeMillis();
            synchronized (NotifyListener.class) {
                if (key.equals(lastKey)) return;
                lastKey = key;
                File file = new File(dir, "incoming.jsonl");
                if (file.length() > 200 * 1024) {
                    file.delete();                       // keep the file small: the oldest messages go
                }
                JSONObject o = new JSONObject();
                o.put("pkg", pkg);
                o.put("title", t);
                o.put("text", body);
                o.put("ts", ts);
                Writer w = new OutputStreamWriter(new FileOutputStream(file, true), "UTF-8");
                w.write(o.toString() + "\n");
                w.close();
            }
            final Context ctx = getApplicationContext();
            final String app = appName(pkg);
            final long when = ts;
            VOICE.execute(new Runnable() {
                public void run() {
                    try {
                        readAloud(ctx, app, t, body, when);
                    } catch (Throwable ignored) {
                    }
                }
            });
        } catch (Throwable ignored) {
        }
    }

    // ------------------------------------------------------------------------------------------ reading aloud

    private static String readFile(File f) {
        try {
            java.io.FileInputStream in = new java.io.FileInputStream(f);
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buf = new byte[4096];
            int n;
            while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
            in.close();
            return out.toString("UTF-8");
        } catch (Throwable ignored) {
            return "";
        }
    }

    private static boolean headphones(Context ctx) {
        try {
            AudioManager am = (AudioManager) ctx.getSystemService(Context.AUDIO_SERVICE);
            AudioDeviceInfo[] devices = am.getDevices(AudioManager.GET_DEVICES_OUTPUTS);
            for (AudioDeviceInfo d : devices) {
                int t = d.getType();
                if (t == AudioDeviceInfo.TYPE_WIRED_HEADSET || t == AudioDeviceInfo.TYPE_WIRED_HEADPHONES
                        || t == AudioDeviceInfo.TYPE_BLUETOOTH_A2DP || t == AudioDeviceInfo.TYPE_BLUETOOTH_SCO
                        || t == AudioDeviceInfo.TYPE_USB_HEADSET) {
                    return true;
                }
            }
        } catch (Throwable ignored) {
        }
        return false;
    }

    private static boolean online(Context ctx) {
        try {
            ConnectivityManager cm = (ConnectivityManager) ctx.getSystemService(Context.CONNECTIVITY_SERVICE);
            NetworkInfo info = cm.getActiveNetworkInfo();
            return info != null && info.isConnected();
        } catch (Throwable ignored) {
            return false;
        }
    }

    /** tg / ru / en for one text (Tajik letters decide first, as in the app). */
    private static String detect(String text, String appLang) {
        String tj = "ғӣқӯҳҷҒӢҚӮҲҶ";
        int letters = 0, cyr = 0;
        for (int i = 0; i < text.length(); i++) {
            char c = text.charAt(i);
            if (tj.indexOf(c) >= 0) return "tg";
            if (Character.isLetter(c)) {
                letters++;
                if (c >= 0x0400 && c <= 0x04ff) cyr++;
            }
        }
        if (letters == 0) return "ru".equals(appLang) || "en".equals(appLang) ? appLang : "tg";
        if (cyr * 2 > letters) {
            if (text.indexOf('ы') >= 0 || text.indexOf('э') >= 0 || text.indexOf('Ы') >= 0 || "ru".equals(appLang)) return "ru";
            return "tg".equals(appLang) ? "tg" : "ru";
        }
        return "en";
    }

    private static void readAloud(Context ctx, String app, String who, String body, long ts) throws Exception {
        File dir = ctx.getFilesDir();
        JSONObject cfg;
        try {
            cfg = new JSONObject(readFile(new File(dir, "reader_cfg.json")));
        } catch (Throwable e) {
            return;
        }
        String mode = cfg.optString("mode", "off");
        if (mode.equals("off")) return;
        File fg = new File(dir, "app_fg");
        if (fg.exists() && System.currentTimeMillis() - fg.lastModified() < 25000) return;   // the app is open: it reads by itself
        if (mode.equals("headphones") && !headphones(ctx)) return;

        String lang = cfg.optString("lang", "tg");
        String intro = cfg.optString("intro", "{app}: {who}");
        String say = body.length() > 320 ? body.substring(0, 320) : body;
        String head = intro.replace("{app}", app).replace("{who}", who.length() > 0 ? who + ", " : "");
        String bodyLang = detect(say, lang);

        boolean done = false;
        String token = cfg.optString("token", "");
        String url = cfg.optString("url", "");
        if (token.length() > 0 && url.length() > 0 && online(ctx)) {
            try {
                File a = fetchAudio(ctx, url, token, head, lang, "a", cfg);
                File b = fetchAudio(ctx, url, token, say, bodyLang, "b", cfg);
                play(ctx, a);
                play(ctx, b);
                a.delete();
                b.delete();
                done = true;
            } catch (Throwable ignored) {
                done = false;
            }
        }
        if (!done) {
            phoneVoice(ctx, head, lang, say, bodyLang);
        }
        try {
            Writer w = new OutputStreamWriter(new FileOutputStream(new File(dir, "spoken_ts.txt")), "UTF-8");
            w.write(String.valueOf(ts));
            w.close();
        } catch (Throwable ignored) {
        }
    }

    private static File fetchAudio(Context ctx, String url, String token, String text, String language, String tag, JSONObject cfg) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        c.setRequestMethod("POST");
        c.setConnectTimeout(8000);
        c.setReadTimeout(25000);
        c.setDoOutput(true);
        c.setRequestProperty("Content-Type", "application/json");
        c.setRequestProperty("Authorization", "Bearer " + token);
        JSONObject o = new JSONObject();
        o.put("text", text);
        o.put("language", language);
        String fv = cfg.optString("fish_voice", "");   // овози интихобшуда
        if (fv.length() > 0) o.put("fish_voice", fv);
        o.put("engine", cfg.optString("engine", "fish"));
        o.put("emotion", cfg.optBoolean("emotion", true));
        byte[] payload = o.toString().getBytes("UTF-8");
        OutputStream os = c.getOutputStream();
        os.write(payload);
        os.close();
        if (c.getResponseCode() != 200) throw new RuntimeException("speak " + c.getResponseCode());
        InputStream in = c.getInputStream();
        File f = new File(ctx.getCacheDir(), "reader_" + tag + ".snd");
        FileOutputStream out = new FileOutputStream(f);
        byte[] buf = new byte[8192];
        int n;
        long total = 0;
        while ((n = in.read(buf)) > 0) {
            out.write(buf, 0, n);
            total += n;
            if (total > 6 * 1024 * 1024) break;
        }
        out.close();
        in.close();
        if (total < 500) throw new RuntimeException("empty audio");
        return f;
    }

    private static void play(Context ctx, File f) throws Exception {
        final CountDownLatch latch = new CountDownLatch(1);
        MediaPlayer mp = new MediaPlayer();
        try {
            mp.setWakeMode(ctx, PowerManager.PARTIAL_WAKE_LOCK);
        } catch (Throwable ignored) {
        }
        mp.setAudioAttributes(new AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA)
                .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build());
        mp.setDataSource(f.getAbsolutePath());
        mp.setOnCompletionListener(new MediaPlayer.OnCompletionListener() {
            public void onCompletion(MediaPlayer m) {
                latch.countDown();
            }
        });
        mp.setOnErrorListener(new MediaPlayer.OnErrorListener() {
            public boolean onError(MediaPlayer m, int what, int extra) {
                latch.countDown();
                return true;
            }
        });
        mp.prepare();
        mp.start();
        latch.await(90, TimeUnit.SECONDS);
        mp.release();
    }

    private static Locale localeFor(String lang) {
        if ("en".equals(lang)) return Locale.ENGLISH;
        return new Locale("ru");          // the phone has no Tajik voice: the Russian one is the closest it can do
    }

    private static void phoneVoice(Context ctx, final String head, String headLang, final String body, String bodyLang) throws Exception {
        final CountDownLatch ready = new CountDownLatch(1);
        final boolean[] ok = new boolean[1];
        final TextToSpeech[] holder = new TextToSpeech[1];
        holder[0] = new TextToSpeech(ctx, new TextToSpeech.OnInitListener() {
            public void onInit(int status) {
                ok[0] = status == TextToSpeech.SUCCESS;
                ready.countDown();
            }
        });
        ready.await(8, TimeUnit.SECONDS);
        if (!ok[0]) {
            holder[0].shutdown();
            return;
        }
        TextToSpeech tts = holder[0];
        final CountDownLatch finished = new CountDownLatch(1);
        tts.setOnUtteranceProgressListener(new UtteranceProgressListener() {
            public void onStart(String id) {
            }

            public void onDone(String id) {
                if ("body".equals(id)) finished.countDown();
            }

            public void onError(String id) {
                finished.countDown();
            }
        });
        tts.setLanguage(localeFor(headLang));
        tts.speak(head, TextToSpeech.QUEUE_FLUSH, null, "head");
        tts.setLanguage(localeFor(bodyLang));
        tts.speak(body, TextToSpeech.QUEUE_ADD, null, "body");
        finished.await(60, TimeUnit.SECONDS);
        tts.shutdown();
    }
}
