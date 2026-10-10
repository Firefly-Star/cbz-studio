package com.cbzstudio;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.ClipData;
import android.content.Context;
import android.content.DialogInterface;
import android.content.Intent;
import android.database.Cursor;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Color;
import android.graphics.Matrix;
import android.graphics.drawable.ColorDrawable;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.provider.OpenableColumns;
import android.util.TypedValue;
import android.view.GestureDetector;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.MotionEvent;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.HorizontalScrollView;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.List;
import java.util.Locale;

/**
 * 边看边改的 cbz 阅读/编辑器（Android 版）。
 *
 * 刻意不依赖任何第三方库 / AndroidX —— 界面全部用框架自带的 widget 代码构建，
 * 这样用 aapt2 + javac + d8 + apksigner 就能直接出 APK，不需要 Gradle 联网拉依赖。
 */
public class MainActivity extends Activity {

    private static final int REQ_OPEN = 1001;
    private static final int REQ_INSERT = 1002;
    private static final int REQ_SAVE = 1003;

    // 配色
    private static final int C_BG = Color.parseColor("#1f1f22");
    private static final int C_BAR = Color.parseColor("#2a2a30");
    private static final int C_FG = Color.parseColor("#e8e8ea");
    private static final int C_DIM = Color.parseColor("#8a8a92");
    private static final int C_ACCENT = Color.parseColor("#5aa9e6");

    private CbzDoc doc;
    private File localCbz;          // 实际打开的本地文件（可能是 cache 里的副本）
    private int cur = 0;
    private boolean dirty = false;

    private ImageView imageView;
    private TextView status;
    private LinearLayout toolbar;
    private Bitmap bmp;
    private final Deque<Runnable> undoStack = new ArrayDeque<Runnable>();
    private GestureDetector gestures;
    private final Handler ui = new Handler(Looper.getMainLooper());

    /** Android 端旋转：Bitmap + Matrix，结果重新编码。 */
    private static class AndroidTranscoder implements CbzDoc.Transcoder {
        public byte[] rotate(byte[] data, String name, int degrees) throws IOException {
            Bitmap src = BitmapFactory.decodeByteArray(data, 0, data.length);
            if (src == null) {
                throw new IOException("无法解码图片: " + name);
            }
            Matrix m = new Matrix();
            m.postRotate(degrees);
            Bitmap out = Bitmap.createBitmap(src, 0, 0, src.getWidth(), src.getHeight(), m, true);
            if (out != src) {
                src.recycle();
            }
            ByteArrayOutputStream bos = new ByteArrayOutputStream();
            Bitmap.CompressFormat fmt = ".png".equals(CbzDoc.extOf(name))
                    ? Bitmap.CompressFormat.PNG : Bitmap.CompressFormat.JPEG;
            out.compress(fmt, 95, bos);
            out.recycle();
            return bos.toByteArray();
        }
    }

    // ------------------------------------------------------------ 生命周期

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        buildUi();
        Uri data = getIntent() == null ? null : getIntent().getData();
        if (data != null) {
            loadUri(data);
        } else {
            status.setText("点「打开」选一个 .cbz 文件");
        }
    }

    @Override
    protected void onDestroy() {
        if (bmp != null) {
            bmp.recycle();
            bmp = null;
        }
        if (doc != null) {
            doc.close();
        }
        super.onDestroy();
    }

    @Override
    public void onBackPressed() {
        if (dirty) {
            new AlertDialog.Builder(this)
                    .setTitle("还有未保存的改动")
                    .setMessage("直接退出会丢掉这次编辑，确定吗？")
                    .setPositiveButton("退出", new DialogInterface.OnClickListener() {
                        public void onClick(DialogInterface d, int w) {
                            finish();
                        }
                    })
                    .setNegativeButton("继续编辑", null)
                    .show();
        } else {
            super.onBackPressed();
        }
    }

    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        // 音量键翻页：单手阅读时最顺手
        if (keyCode == KeyEvent.KEYCODE_VOLUME_DOWN) {
            next();
            return true;
        }
        if (keyCode == KeyEvent.KEYCODE_VOLUME_UP) {
            prev();
            return true;
        }
        return super.onKeyDown(keyCode, event);
    }

    // ------------------------------------------------------------ 界面

    private int dp(float v) {
        return (int) TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v,
                getResources().getDisplayMetrics());
    }

    private Button mkButton(String text, View.OnClickListener l) {
        Button b = new Button(this);
        b.setText(text);
        b.setAllCaps(false);
        b.setTextSize(TypedValue.COMPLEX_UNIT_SP, 13);
        b.setTextColor(C_FG);
        b.setBackgroundColor(C_BAR);
        b.setOnClickListener(l);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.setMargins(dp(2), dp(4), dp(2), dp(4));
        b.setLayoutParams(lp);
        b.setMinWidth(dp(56));
        b.setMinimumWidth(dp(56));
        return b;
    }

    private void buildUi() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(C_BG);

        // 顶部状态栏
        status = new TextView(this);
        status.setTextColor(C_ACCENT);
        status.setTextSize(TypedValue.COMPLEX_UNIT_SP, 12);
        status.setPadding(dp(10), dp(6), dp(10), dp(6));
        status.setBackgroundColor(C_BAR);
        root.addView(status, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        // 图片区
        FrameLayout stage = new FrameLayout(this);
        stage.setBackgroundColor(Color.BLACK);
        imageView = new ImageView(this);
        imageView.setScaleType(ImageView.ScaleType.FIT_CENTER);
        imageView.setBackgroundDrawable(new ColorDrawable(Color.BLACK));
        stage.addView(imageView, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        root.addView(stage, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));

        // 工具条（横向可滚动，手机上一行放不下）
        HorizontalScrollView hs = new HorizontalScrollView(this);
        hs.setBackgroundColor(C_BAR);
        toolbar = new LinearLayout(this);
        toolbar.setOrientation(LinearLayout.HORIZONTAL);
        toolbar.setPadding(dp(4), 0, dp(4), 0);
        hs.addView(toolbar);
        root.addView(hs, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        toolbar.addView(mkButton("打开", new View.OnClickListener() {
            public void onClick(View v) {
                Intent i = new Intent(Intent.ACTION_OPEN_DOCUMENT);
                i.addCategory(Intent.CATEGORY_OPENABLE);
                i.setType("*/*");
                startActivityForResult(i, REQ_OPEN);
            }
        }));
        toolbar.addView(mkButton("保存", new View.OnClickListener() {
            public void onClick(View v) {
                saveAs();
            }
        }));
        toolbar.addView(mkButton("撤销", new View.OnClickListener() {
            public void onClick(View v) {
                undo();
            }
        }));
        toolbar.addView(mkButton("◀", new View.OnClickListener() {
            public void onClick(View v) {
                prev();
            }
        }));
        toolbar.addView(mkButton("▶", new View.OnClickListener() {
            public void onClick(View v) {
                next();
            }
        }));
        toolbar.addView(mkButton("删除", new View.OnClickListener() {
            public void onClick(View v) {
                deleteCur();
            }
        }));
        toolbar.addView(mkButton("旋转", new View.OnClickListener() {
            public void onClick(View v) {
                rotateCur(90);
            }
        }));
        toolbar.addView(mkButton("上移", new View.OnClickListener() {
            public void onClick(View v) {
                moveCur(-1);
            }
        }));
        toolbar.addView(mkButton("下移", new View.OnClickListener() {
            public void onClick(View v) {
                moveCur(1);
            }
        }));
        toolbar.addView(mkButton("插页", new View.OnClickListener() {
            public void onClick(View v) {
                Intent i = new Intent(Intent.ACTION_OPEN_DOCUMENT);
                i.addCategory(Intent.CATEGORY_OPENABLE);
                i.setType("image/*");
                i.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true);
                startActivityForResult(i, REQ_INSERT);
            }
        }));
        toolbar.addView(mkButton("章节", new View.OnClickListener() {
            public void onClick(View v) {
                askDir();
            }
        }));
        toolbar.addView(mkButton("上一话", new View.OnClickListener() {
            public void onClick(View v) {
                jumpChapter(-1);
            }
        }));
        toolbar.addView(mkButton("下一话", new View.OnClickListener() {
            public void onClick(View v) {
                jumpChapter(1);
            }
        }));
        toolbar.addView(mkButton("右开", new View.OnClickListener() {
            public void onClick(View v) {
                if (doc != null) {
                    doc.rtl = !doc.rtl;
                    updateStatus();
                }
            }
        }));
        toolbar.addView(mkButton("首页", new View.OnClickListener() {
            public void onClick(View v) {
                gotoLive(0);
            }
        }));
        toolbar.addView(mkButton("末页", new View.OnClickListener() {
            public void onClick(View v) {
                List<CbzDoc.Page> live = liveList();
                if (!live.isEmpty()) {
                    cur = indexOf(live.get(live.size() - 1));
                    showPage();
                }
            }
        }));

        setContentView(root);

        // 手势：左右滑动翻页，点左/右 1/3 也翻页，点中间切换工具条
        gestures = new GestureDetector(this, new GestureDetector.SimpleOnGestureListener() {
            @Override
            public boolean onDown(MotionEvent e) {
                return true;
            }

            @Override
            public boolean onFling(MotionEvent e1, MotionEvent e2, float vx, float vy) {
                if (e1 == null || e2 == null) {
                    return false;
                }
                float dx = e2.getX() - e1.getX();
                if (Math.abs(dx) < dp(48) || Math.abs(vx) < 200) {
                    return false;
                }
                boolean next = doc != null && doc.rtl ? dx > 0 : dx < 0;
                if (next) {
                    next();
                } else {
                    prev();
                }
                return true;
            }

            @Override
            public boolean onSingleTapUp(MotionEvent e) {
                int w = stage.getWidth();
                float x = e.getX();
                if (x < w * 0.3f) {
                    if (doc != null && doc.rtl) {
                        next();
                    } else {
                        prev();
                    }
                } else if (x > w * 0.7f) {
                    if (doc != null && doc.rtl) {
                        prev();
                    } else {
                        next();
                    }
                } else {
                    toolbar.setVisibility(toolbar.getVisibility() == View.VISIBLE
                            ? View.GONE : View.VISIBLE);
                }
                return true;
            }
        });
        stage.setOnTouchListener(new View.OnTouchListener() {
            public boolean onTouch(View v, MotionEvent e) {
                return gestures.onTouchEvent(e);
            }
        });
    }

    // ------------------------------------------------------------ 打开 / 载入

    @Override
    protected void onActivityResult(int req, int res, Intent data) {
        super.onActivityResult(req, res, data);
        if (res != RESULT_OK || data == null) {
            return;
        }
        if (req == REQ_OPEN && data.getData() != null) {
            loadUri(data.getData());
        } else if (req == REQ_INSERT) {
            List<Uri> uris = new ArrayList<Uri>();
            ClipData clip = data.getClipData();
            if (clip != null) {
                for (int i = 0; i < clip.getItemCount(); i++) {
                    uris.add(clip.getItemAt(i).getUri());
                }
            } else if (data.getData() != null) {
                uris.add(data.getData());
            }
            insertUris(uris);
        } else if (req == REQ_SAVE && data.getData() != null) {
            writeTo(data.getData());
        }
    }

    /** file:// 直接就地打开，content:// 先拷到 cacheDir。 */
    private void loadUri(Uri uri) {
        if (dirty && doc != null) {
            Toast.makeText(this, "之前的改动未保存，已丢弃", Toast.LENGTH_SHORT).show();
        }
        // 大包（几百 MB）拷贝 + 建索引可能要好几秒，放后台线程，免得 ANR
        final Uri target = uri;
        setBusy(true);
        status.setText("正在载入…");
        new Thread(new Runnable() {
            public void run() {
                try {
                    final File f = materialize(target);
                    final CbzDoc d = new CbzDoc(f);
                    ui.post(new Runnable() {
                        public void run() {
                            if (doc != null) {
                                doc.close();
                            }
                            doc = d;
                            localCbz = f;
                            cur = 0;
                            dirty = false;
                            undoStack.clear();
                            List<CbzDoc.Page> live = liveList();
                            if (!live.isEmpty()) {
                                cur = indexOf(live.get(0));
                            }
                            setBusy(false);
                            showPage();
                            Toast.makeText(MainActivity.this,
                                    "已载入 " + live.size() + " 页", Toast.LENGTH_SHORT).show();
                        }
                    });
                } catch (final Exception e) {
                    ui.post(new Runnable() {
                        public void run() {
                            setBusy(false);
                            fail("打开失败", e);
                            updateStatus();
                        }
                    });
                }
            }
        }).start();
    }

    /** 载入/保存期间把工具条按钮禁用掉，避免并发改到一半的文档。 */
    private void setBusy(boolean busy) {
        if (toolbar == null) {
            return;
        }
        for (int i = 0; i < toolbar.getChildCount(); i++) {
            toolbar.getChildAt(i).setEnabled(!busy);
        }
    }

    private File materialize(Uri uri) throws IOException {
        String scheme = uri.getScheme();
        if ("file".equals(scheme) && uri.getPath() != null) {
            File f = new File(uri.getPath());
            if (f.isFile()) {
                return f;
            }
        }
        String name = queryName(uri);
        if (name == null || name.length() == 0) {
            name = "opened.cbz";
        }
        File dst = new File(getCacheDir(), sanitize(name));
        InputStream in = getContentResolver().openInputStream(uri);
        if (in == null) {
            throw new IOException("无法读取该文件");
        }
        try {
            OutputStream out = new FileOutputStream(dst);
            try {
                byte[] buf = new byte[256 * 1024];
                int n;
                while ((n = in.read(buf)) > 0) {
                    out.write(buf, 0, n);
                }
            } finally {
                out.close();
            }
        } finally {
            in.close();
        }
        return dst;
    }

    private String queryName(Uri uri) {
        Cursor c = null;
        try {
            c = getContentResolver().query(uri, null, null, null, null);
            if (c != null && c.moveToFirst()) {
                int idx = c.getColumnIndex(OpenableColumns.DISPLAY_NAME);
                if (idx >= 0) {
                    return c.getString(idx);
                }
            }
        } catch (Exception ignored) {
        } finally {
            if (c != null) {
                c.close();
            }
        }
        return null;
    }

    private static String sanitize(String name) {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < name.length(); i++) {
            char ch = name.charAt(i);
            sb.append("\\/:*?\"<>|".indexOf(ch) >= 0 ? '_' : ch);
        }
        return sb.length() == 0 ? "opened.cbz" : sb.toString();
    }

    private void fail(String title, Exception e) {
        String msg = e.getMessage() == null ? e.toString() : e.getMessage();
        new AlertDialog.Builder(this).setTitle(title).setMessage(msg)
                .setPositiveButton("好", null).show();
    }

    // ------------------------------------------------------------ 导航

    private List<CbzDoc.Page> liveList() {
        return doc == null ? new ArrayList<CbzDoc.Page>() : doc.live();
    }

    private int indexOf(CbzDoc.Page p) {
        for (int i = 0; i < doc.pages.size(); i++) {
            if (doc.pages.get(i) == p) {
                return i;
            }
        }
        return 0;
    }

    private int livePos() {
        List<CbzDoc.Page> live = liveList();
        for (int i = 0; i < live.size(); i++) {
            if (indexOf(live.get(i)) == cur) {
                return i;
            }
        }
        return -1;
    }

    private void gotoLive(int pos) {
        List<CbzDoc.Page> live = liveList();
        if (live.isEmpty()) {
            return;
        }
        pos = Math.max(0, Math.min(pos, live.size() - 1));
        cur = indexOf(live.get(pos));
        showPage();
    }

    private void next() {
        gotoLive(livePos() + 1);
    }

    private void prev() {
        gotoLive(livePos() - 1);
    }

    private void jumpChapter(int dir) {
        if (doc == null) {
            return;
        }
        List<CbzDoc.Page> live = liveList();
        int pos = livePos();
        if (pos < 0) {
            gotoLive(0);
            return;
        }
        String curDir = CbzDoc.dirOf(live.get(pos).outName);
        if (dir > 0) {
            for (int i = pos + 1; i < live.size(); i++) {
                if (!CbzDoc.dirOf(live.get(i).outName).equals(curDir)) {
                    gotoLive(i);
                    return;
                }
            }
            gotoLive(live.size() - 1);
        } else {
            for (int i = pos - 1; i >= 0; i--) {
                if (!CbzDoc.dirOf(live.get(i).outName).equals(curDir)) {
                    String d = CbzDoc.dirOf(live.get(i).outName);
                    int j = i;
                    while (j > 0 && CbzDoc.dirOf(live.get(j - 1).outName).equals(d)) {
                        j--;
                    }
                    gotoLive(j);
                    return;
                }
            }
            gotoLive(0);
        }
    }

    // ------------------------------------------------------------ 显示

    private void showPage() {
        if (doc == null) {
            return;
        }
        List<CbzDoc.Page> live = liveList();
        if (live.isEmpty()) {
            return;
        }
        int pos = livePos();
        if (pos < 0) {
            cur = indexOf(live.get(0));
            pos = 0;
        }
        CbzDoc.Page page = doc.pages.get(cur);
        try {
            byte[] data = doc.read(page);
            BitmapFactory.Options o = new BitmapFactory.Options();
            o.inJustDecodeBounds = true;
            BitmapFactory.decodeByteArray(data, 0, data.length, o);
            int max = 2048;
            int sample = 1;
            while (o.outWidth / sample > max || o.outHeight / sample > max) {
                sample *= 2;
            }
            BitmapFactory.Options o2 = new BitmapFactory.Options();
            o2.inSampleSize = sample;
            o2.inPreferredConfig = Bitmap.Config.RGB_565;
            Bitmap decoded = BitmapFactory.decodeByteArray(data, 0, data.length, o2);
            if (decoded == null) {
                throw new IOException("这一页解不出来: " + page.outName);
            }
            if (page.rotation % 360 != 0) {
                Matrix m = new Matrix();
                m.postRotate(page.rotation % 360);
                Bitmap rotated = Bitmap.createBitmap(decoded, 0, 0,
                        decoded.getWidth(), decoded.getHeight(), m, true);
                if (rotated != decoded) {
                    decoded.recycle();
                }
                decoded = rotated;
            }
            if (bmp != null) {
                bmp.recycle();
            }
            bmp = decoded;
            imageView.setImageBitmap(bmp);
        } catch (Exception e) {
            fail("读取这一页失败", e);
        }
        updateStatus();
    }

    private void updateStatus() {
        if (doc == null || status == null) {
            return;
        }
        List<CbzDoc.Page> live = liveList();
        CbzDoc.Page p = doc.pages.get(cur);
        String size = bmp == null ? "-" : bmp.getWidth() + "x" + bmp.getHeight();
        StringBuilder sb = new StringBuilder();
        sb.append('第').append(livePos() + 1).append('/').append(live.size()).append("页");
        sb.append(" · ").append(size);
        sb.append(" · 目录 ").append(CbzDoc.dirOf(p.outName).isEmpty()
                ? "(根)" : CbzDoc.dirOf(p.outName));
        if (p.rotation % 360 != 0) {
            sb.append(" · 转").append(p.rotation % 360).append('°');
        }
        if (p.isNew()) {
            sb.append(" · 新插入");
        }
        if (doc.deletedCount() > 0) {
            sb.append(" · 待删").append(doc.deletedCount());
        }
        sb.append(doc.rtl ? " · 右开" : " · 左开");
        if (dirty) {
            sb.append(" · 未保存*");
        }
        status.setText(sb.toString());
    }

    // ------------------------------------------------------------ 编辑

    private void deleteCur() {
        if (doc == null) {
            return;
        }
        if (liveList().size() <= 1) {
            Toast.makeText(this, "至少要留一页", Toast.LENGTH_SHORT).show();
            return;
        }
        final CbzDoc.Page p = doc.pages.get(cur);
        if (p.deleted) {
            return;
        }
        final int pos = livePos();          // 记下删除前的位置
        doc.delete(cur);
        dirty = true;
        undoStack.push(new Runnable() {
            public void run() {
                p.deleted = false;
            }
        });
        // 保持阅读位置：停在同一个序号上（此时显示的是被删页的下一页），
        // 而不是因为 livePos() 找不到当前页就弹回第一页
        List<CbzDoc.Page> after = liveList();
        if (after.isEmpty()) {
            return;
        }
        cur = indexOf(after.get(Math.min(Math.max(pos, 0), after.size() - 1)));
        showPage();
    }

    private void undo() {
        if (undoStack.isEmpty()) {
            Toast.makeText(this, "没有可撤销的操作", Toast.LENGTH_SHORT).show();
            return;
        }
        undoStack.pop().run();
        dirty = true;
        showPage();
        Toast.makeText(this, "已撤销", Toast.LENGTH_SHORT).show();
    }

    private void rotateCur(int delta) {
        if (doc == null) {
            return;
        }
        final CbzDoc.Page p = doc.pages.get(cur);
        final int before = p.rotation;
        doc.rotate(cur, delta);
        dirty = true;
        undoStack.push(new Runnable() {
            public void run() {
                p.rotation = before;
            }
        });
        showPage();
    }

    private void moveCur(int delta) {
        if (doc == null) {
            return;
        }
        final int from = cur;
        final int to = doc.move(cur, delta);
        if (to == from) {
            return;
        }
        dirty = true;
        undoStack.push(new Runnable() {
            public void run() {
                doc.move(to, from - to);
            }
        });
        cur = to;
        showPage();
    }

    private void insertUris(List<Uri> uris) {
        if (doc == null || uris.isEmpty()) {
            return;
        }
        final List<CbzDoc.Page> added = new ArrayList<CbzDoc.Page>();
        String dir = CbzDoc.dirOf(doc.pages.get(cur).outName);
        final int at = cur + 1;
        int k = 0;
        for (Uri u : uris) {
            try {
                File f = materialize(u);
                String name = queryName(u);
                if (name == null) {
                    name = f.getName();
                }
                CbzDoc.Page p = new CbzDoc.Page(null, CbzDoc.withDir(sanitize(name), dir));
                p.source = f;
                added.add(p);
                k++;
            } catch (Exception e) {
                fail("插入这张图失败", e);
            }
        }
        if (added.isEmpty()) {
            return;
        }
        for (int i = 0; i < added.size(); i++) {
            doc.insert(at + i, added.get(i));
        }
        dirty = true;
        undoStack.push(new Runnable() {
            public void run() {
                for (CbzDoc.Page p : added) {
                    doc.pages.remove(p);
                }
            }
        });
        cur = at;
        showPage();
        Toast.makeText(this, "已插入 " + k + " 页到 " + (dir.isEmpty() ? "根目录" : dir),
                Toast.LENGTH_SHORT).show();
    }

    private void askDir() {
        if (doc == null) {
            return;
        }
        final CbzDoc.Page p = doc.pages.get(cur);
        final EditText input = new EditText(this);
        input.setText(CbzDoc.dirOf(p.outName));
        input.setHint("例如 001_第01话；留空 = 根目录");
        input.setTextColor(C_FG);
        new AlertDialog.Builder(this)
                .setTitle("把这一页放到哪个章节目录？")
                .setView(input)
                .setPositiveButton("确定", new DialogInterface.OnClickListener() {
                    public void onClick(DialogInterface d, int w) {
                        final String oldName = p.outName;
                        doc.setDir(cur, input.getText().toString());
                        dirty = true;
                        undoStack.push(new Runnable() {
                            public void run() {
                                p.outName = oldName;
                            }
                        });
                        showPage();
                    }
                })
                .setNegativeButton("取消", null)
                .show();
    }

    // ------------------------------------------------------------ 保存

    private void saveAs() {
        if (doc == null) {
            return;
        }
        String base = localCbz == null ? "output" : localCbz.getName();
        if (base.toLowerCase(Locale.ROOT).endsWith(".cbz")) {
            base = base.substring(0, base.length() - 4);
        }
        Intent i = new Intent(Intent.ACTION_CREATE_DOCUMENT);
        i.addCategory(Intent.CATEGORY_OPENABLE);
        i.setType("application/octet-stream");
        i.putExtra(Intent.EXTRA_TITLE, base + ".edited.cbz");
        startActivityForResult(i, REQ_SAVE);
    }

    private void writeTo(final Uri target) {
        status.setText("正在保存…");
        final CbzDoc d = doc;
        final boolean renumber = true;
        new Thread(new Runnable() {
            public void run() {
                File tmp = new File(getCacheDir(), "saving.cbz");
                try {
                    final int n = d.save(tmp, renumber, 4, new AndroidTranscoder(), null);
                    OutputStream out = getContentResolver().openOutputStream(target);
                    if (out == null) {
                        throw new IOException("无法写入目标文件");
                    }
                    try {
                        InputStream in = new java.io.FileInputStream(tmp);
                        try {
                            byte[] buf = new byte[256 * 1024];
                            int r;
                            while ((r = in.read(buf)) > 0) {
                                out.write(buf, 0, r);
                            }
                        } finally {
                            in.close();
                        }
                    } finally {
                        out.close();
                    }
                    if (!tmp.delete()) {
                        tmp.deleteOnExit();
                    }
                    ui.post(new Runnable() {
                        public void run() {
                            dirty = false;
                            Toast.makeText(MainActivity.this,
                                    "已保存 " + n + " 页", Toast.LENGTH_LONG).show();
                            updateStatus();
                        }
                    });
                } catch (final Exception e) {
                    // save() 可能已关掉源句柄，重开保证界面还能继续用
                    ui.post(new Runnable() {
                        public void run() {
                            fail("保存失败", e);
                            updateStatus();
                        }
                    });
                }
            }
        }).start();
    }
}
