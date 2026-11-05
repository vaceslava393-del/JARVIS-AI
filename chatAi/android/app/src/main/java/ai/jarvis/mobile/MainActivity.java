package ai.jarvis.mobile;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.Bundle;
import android.speech.RecognizerIntent;
import android.speech.tts.TextToSpeech;
import android.view.Menu;
import android.view.MenuItem;
import android.webkit.HttpAuthHandler;
import android.webkit.JavascriptInterface;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.graphics.Color;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;
import java.util.ArrayList;
import java.util.Locale;

public class MainActivity extends Activity implements TextToSpeech.OnInitListener {
    private static final int SPEECH_REQUEST = 42;
    private WebView webView;
    private TextToSpeech tts;
    private String serverUrl;
    private boolean ttsReady = false;
    private String pendingSpeech = null;
    private float pendingRate = 1.0f;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        serverUrl = getPreferences(MODE_PRIVATE).getString("server", "");
        tts = new TextToSpeech(this, this);
        webView = new WebView(this);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Color.rgb(2, 7, 17));
        LinearLayout bar = new LinearLayout(this);
        bar.setGravity(android.view.Gravity.CENTER_VERTICAL);
        bar.setPadding(20, 8, 12, 8);
        TextView title = new TextView(this);
        title.setText("JARVIS AI"); title.setTextColor(Color.rgb(97, 219, 255)); title.setTextSize(20); title.setTypeface(null, android.graphics.Typeface.BOLD);
        bar.addView(title, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));
        Button settings = new Button(this); settings.setText("СЕРВЕР"); settings.setOnClickListener(v -> showServerDialog()); bar.addView(settings);
        root.addView(bar, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        root.addView(webView, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1));
        setContentView(root);
        webView.getSettings().setJavaScriptEnabled(true);
        webView.getSettings().setDomStorageEnabled(true);
        webView.getSettings().setMediaPlaybackRequiresUserGesture(false);
        webView.setWebChromeClient(new WebChromeClient());
        webView.addJavascriptInterface(new TtsBridge(), "AndroidTTS");
        webView.addJavascriptInterface(new SpeechBridge(), "AndroidSpeech");
        webView.addJavascriptInterface(new ConfigBridge(), "AndroidConfig");
        webView.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                String url=request.getUrl().toString();
                if (!serverUrl.isEmpty() && url.startsWith(serverUrl)) return false;
                startActivity(new Intent(Intent.ACTION_VIEW, request.getUrl())); return true;
            }
            @Override public void onReceivedHttpAuthRequest(WebView view, HttpAuthHandler handler, String host, String realm) { showLogin(handler, host); }
            @Override public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) showConnectionError();
            }
        });
        if (serverUrl.isEmpty()) {
            showConnectionError();
            webView.postDelayed(this::showServerDialog, 250);
        } else webView.loadUrl(serverUrl);
    }

    @Override public boolean onCreateOptionsMenu(Menu menu) { menu.add("Адрес сервера").setShowAsAction(MenuItem.SHOW_AS_ACTION_ALWAYS); return true; }
    @Override public boolean onOptionsItemSelected(MenuItem item) { showServerDialog(); return true; }
    private void showServerDialog() {
        EditText input=new EditText(this); input.setText(serverUrl.isEmpty() ? "http://192.168.8.60:9014" : serverUrl); input.setSingleLine(true); input.setHint("http://192.168.1.10:9014");
        new AlertDialog.Builder(this).setTitle("Сервер JARVIS").setMessage("Телефон и компьютер должны быть в одной сети. На эмуляторе используйте 10.0.2.2.").setView(input).setPositiveButton("Подключиться",(d,w)->{String url=input.getText().toString().trim().replaceAll("/+$","");if(!url.matches("https?://.+")){Toast.makeText(this,"Нужен адрес http:// или https://",Toast.LENGTH_LONG).show();return;}serverUrl=url;getPreferences(MODE_PRIVATE).edit().putString("server",url).apply();webView.loadUrl(url);}).setNegativeButton("Отмена",null).show();
    }
    private void showConnectionError() {
        String shown = serverUrl.isEmpty() ? "сервер не выбран" : serverUrl;
        String html = "<!doctype html><html><meta name='viewport' content='width=device-width'><body style='background:#020711;color:#eefaff;font-family:sans-serif;padding:28px'>"
                + "<h2 style='color:#61dbff'>Нет подключения к JARVIS</h2><p>Адрес: " + android.text.TextUtils.htmlEncode(shown) + "</p>"
                + "<p>Запустите JARVIS_AI.py на компьютере и подключите телефон к той же Wi-Fi сети.</p>"
                + "<button style='padding:14px;background:#1677ff;color:white;border:0;border-radius:9px' onclick='AndroidConfig.open()'>Указать адрес сервера</button></body></html>";
        webView.loadDataWithBaseURL(null, html, "text/html", "UTF-8", null);
    }
    private void showLogin(HttpAuthHandler handler,String host) {
        LinearLayout box=new LinearLayout(this); box.setOrientation(LinearLayout.VERTICAL); int p=32; box.setPadding(p,0,p,0);
        EditText user=new EditText(this); user.setHint("Логин"); EditText pass=new EditText(this); pass.setHint("Пароль"); pass.setInputType(0x81); box.addView(user);box.addView(pass);
        new AlertDialog.Builder(this).setTitle("Вход в JARVIS — "+host).setView(box).setPositiveButton("Войти",(d,w)->handler.proceed(user.getText().toString(),pass.getText().toString())).setNegativeButton("Отмена",(d,w)->handler.cancel()).setOnCancelListener(d->handler.cancel()).show();
    }
    @Override public void onInit(int status) {
        if (status != TextToSpeech.SUCCESS) {
            Toast.makeText(this, "Не удалось запустить синтез речи Android", Toast.LENGTH_LONG).show();
            return;
        }
        int language = tts.setLanguage(new Locale("ru", "RU"));
        if (language == TextToSpeech.LANG_MISSING_DATA || language == TextToSpeech.LANG_NOT_SUPPORTED) {
            tts.setLanguage(Locale.getDefault());
            Toast.makeText(this, "Русский голос не установлен. Используется системный голос.", Toast.LENGTH_LONG).show();
        }
        ttsReady = true;
        if (pendingSpeech != null) {
            speakNative(pendingSpeech, pendingRate);
            pendingSpeech = null;
        }
    }
    private void speakNative(String text, float rate) {
        if (!ttsReady) {
            pendingSpeech = text;
            pendingRate = rate;
            return;
        }
        tts.setSpeechRate(Math.max(.5f, Math.min(1.5f, rate)));
        int result = tts.speak(text, TextToSpeech.QUEUE_FLUSH, null, "jarvis-" + System.currentTimeMillis());
        if (result == TextToSpeech.ERROR) Toast.makeText(this, "Ошибка воспроизведения голоса", Toast.LENGTH_LONG).show();
    }
    public final class TtsBridge {
        @JavascriptInterface public void speak(String text,double rate) { runOnUiThread(()->speakNative(text, (float)rate)); }
        @JavascriptInterface public void stop() { runOnUiThread(()->tts.stop()); }
        @JavascriptInterface public boolean isReady() { return ttsReady; }
    }
    public final class SpeechBridge { @JavascriptInterface public void listen() { runOnUiThread(()->startSpeech()); } }
    public final class ConfigBridge { @JavascriptInterface public void open() { runOnUiThread(()->showServerDialog()); } }
    private void startSpeech() {
        if(android.os.Build.VERSION.SDK_INT>=23 && checkSelfPermission(Manifest.permission.RECORD_AUDIO)!=PackageManager.PERMISSION_GRANTED){requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO},7);return;}
        Intent i=new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH);i.putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL,RecognizerIntent.LANGUAGE_MODEL_FREE_FORM);i.putExtra(RecognizerIntent.EXTRA_LANGUAGE,"ru-RU");i.putExtra(RecognizerIntent.EXTRA_PROMPT,"Говорите…");try{startActivityForResult(i,SPEECH_REQUEST);}catch(Exception e){Toast.makeText(this,"На устройстве нет сервиса распознавания речи",Toast.LENGTH_LONG).show();}
    }
    @Override protected void onActivityResult(int request,int result,Intent data){super.onActivityResult(request,result,data);if(request==SPEECH_REQUEST&&result==RESULT_OK&&data!=null){ArrayList<String> words=data.getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS);if(words!=null&&!words.isEmpty()){String safe=words.get(0).replace("\\","\\\\").replace("'","\\'").replace("\n"," ");webView.evaluateJavascript("window.jarvisSpeechResult('"+safe+"')",null);}}}
    @Override public void onBackPressed(){if(webView.canGoBack())webView.goBack();else super.onBackPressed();}
    @Override protected void onDestroy(){if(tts!=null){tts.stop();tts.shutdown();}webView.destroy();super.onDestroy();}
}
