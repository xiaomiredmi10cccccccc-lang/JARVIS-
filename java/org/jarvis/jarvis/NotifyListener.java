package org.jarvis.jarvis;

import android.app.Notification;
import android.os.Bundle;
import android.service.notification.NotificationListenerService;
import android.service.notification.StatusBarNotification;

import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.io.OutputStreamWriter;
import java.io.Writer;

/**
 * Collects the text of incoming message notifications (WhatsApp, Telegram, SMS, e-mail ...) into files/incoming.jsonl.
 * Nothing is collected unless the file files/reader_on exists (the switch in the app's settings creates and deletes it).
 * The data never leaves the phone from here: the app reads the file and answers only when the user asks.
 */
public class NotifyListener extends NotificationListenerService {
    private static String lastKey = "";

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
            String t = title == null ? "" : title.toString();
            String body = text.toString();
            if (body.length() > 600) body = body.substring(0, 600);
            String key = pkg + "|" + t + "|" + body;
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
                o.put("ts", System.currentTimeMillis());
                Writer w = new OutputStreamWriter(new FileOutputStream(file, true), "UTF-8");
                w.write(o.toString() + "\n");
                w.close();
            }
        } catch (Throwable ignored) {
        }
    }
}
