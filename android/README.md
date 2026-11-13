# TodoBot Android scaffold

This directory is a documented placeholder for a native Android client. It contains no credentials and does not change the existing backend, worker, or frontend.

## Build

Install Android Studio or the Android SDK, then run:

```bash
./gradlew assembleDebug
```

The generated debug APK is under `app/build/outputs/apk/debug/`. Configure the WebView base URL in `BuildConfig` or a future app settings screen; keep production traffic behind HTTPS. Firebase Cloud Messaging is represented only by a placeholder service and requires a locally supplied `google-services.json` (ignored by Git) before real push notifications can be added.
