package com.todobot.app;

import android.app.Service;
import android.content.Intent;
import android.os.IBinder;

/** FCM integration placeholder; it performs no network calls and stores no secrets. */
public final class TodoBotMessagingService extends Service {
    @Override public IBinder onBind(Intent intent) { return null; }
}
