package com.cbzstudio;

import java.io.BufferedInputStream;
import java.io.BufferedOutputStream;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.util.ArrayList;
import java.util.Enumeration;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.zip.Deflater;
import java.util.zip.ZipEntry;
import java.util.zip.ZipFile;
import java.util.zip.ZipOutputStream;

/**
 * cbz 文档模型：增删页 / 旋转 / 重排 / 改章节目录 / 重编号 / 保存。
 *
 * 这里刻意只用 java.* —— 不引用任何 android.* API，所以同一份代码可以在桌面 JVM 上
 * 直接跑测试（tools/jvmtest/CbzDocTest.java），Android 端再注入用 Bitmap 实现的
 * {@link Transcoder} 完成旋转重编码。
 *
 * 所有编辑都只是改内存里的标记，只有 {@link #save} 才真正写盘。
 */
public class CbzDoc {

    /** 旋转重编码钩子：Android 用 Bitmap，桌面测试用 ImageIO。 */
    public interface Transcoder {
        byte[] rotate(byte[] data, String name, int degrees) throws IOException;
    }

    public interface Progress {
        void onProgress(int done, int total);
    }

    public static final Set<String> IMAGE_EXT = new HashSet<String>();

    static {
        String[] exts = {".jpg", ".jpeg", ".jpe", ".png", ".webp", ".gif",
                         ".bmp", ".tif", ".tiff"};
        for (String e : exts) {
            IMAGE_EXT.add(e);
        }
    }

    /** 一页。deleted / rotation 是标记，保存时才落盘。 */
    public static class Page {
        public String zipName;   // 包内原始条目名；新插入的为 null
        public String outName;   // 保存时写出的条目名
        public File source;      // 新插入图片的本地文件
        public int rotation;     // 顺时针角度
        public boolean deleted;

        public Page(String zipName, String outName) {
            this.zipName = zipName;
            this.outName = outName;
        }

        public boolean isNew() {
            return zipName == null;
        }

        /** 位图缓存用的 key。 */
        public String key() {
            return isNew() ? source.getAbsolutePath() : zipName;
        }

        public String displayName() {
            return baseName(outName);
        }
    }

    public final File cbzFile;
    public final List<Page> pages = new ArrayList<Page>();
    public final List<String> keepEntries = new ArrayList<String>();
    public boolean rtl = true;

    private ZipFile zip;

    public CbzDoc(File cbz) throws IOException {
        this.cbzFile = cbz;
        this.zip = new ZipFile(cbz);
        Enumeration<? extends ZipEntry> en = zip.entries();
        while (en.hasMoreElements()) {
            ZipEntry e = en.nextElement();
            if (e.isDirectory()) {
                continue;
            }
            String n = e.getName();
            if (isImage(n)) {
                pages.add(new Page(n, n));
            } else {
                keepEntries.add(n);
            }
        }
        if (pages.isEmpty()) {
            throw new IOException("包里没有图片: " + cbz.getName());
        }
        rtl = detectRtl();
    }

    // ------------------------------------------------------------ 只读

    public static boolean isImage(String name) {
        int dot = name.lastIndexOf('.');
        if (dot < 0) {
            return false;
        }
        return IMAGE_EXT.contains(name.substring(dot).toLowerCase(Locale.ROOT));
    }

    public static String dirOf(String name) {
        int slash = name.lastIndexOf('/');
        return slash < 0 ? "" : name.substring(0, slash);
    }

    public static String baseName(String name) {
        int slash = name.lastIndexOf('/');
        return slash < 0 ? name : name.substring(slash + 1);
    }

    public static String extOf(String name) {
        int dot = name.lastIndexOf('.');
        return dot < 0 ? "" : name.substring(dot).toLowerCase(Locale.ROOT);
    }

    /** 把 name 放到目录 dir 下（dir 为 null/空 表示根目录）。 */
    public static String withDir(String name, String dir) {
        String base = baseName(name);
        if (dir == null) {
            return base;
        }
        String d = dir.trim();
        while (d.startsWith("/")) {
            d = d.substring(1);
        }
        while (d.endsWith("/")) {
            d = d.substring(0, d.length() - 1);
        }
        return d.isEmpty() ? base : d + "/" + base;
    }

    public List<Page> live() {
        List<Page> out = new ArrayList<Page>();
        for (Page p : pages) {
            if (!p.deleted) {
                out.add(p);
            }
        }
        return out;
    }

    public int deletedCount() {
        int n = 0;
        for (Page p : pages) {
            if (p.deleted) {
                n++;
            }
        }
        return n;
    }

    public byte[] read(Page p) throws IOException {
        if (p.isNew()) {
            return readFile(p.source);
        }
        return readEntry(p.zipName);
    }

    public byte[] readEntry(String name) throws IOException {
        InputStream in = zip.getInputStream(zip.getEntry(name));
        try {
            return readAll(in);
        } finally {
            close(in);
        }
    }

    private boolean detectRtl() {
        for (String name : keepEntries) {
            if (name.toLowerCase(Locale.ROOT).endsWith("comicinfo.xml")) {
                try {
                    String xml = new String(readEntry(name), "UTF-8");
                    return xml.contains("RightToLeft");
                } catch (IOException ignored) {
                    return true;
                }
            }
        }
        return true;
    }

    // ------------------------------------------------------------ 编辑操作

    public void delete(int index) {
        pages.get(index).deleted = true;
    }

    public void undelete(int index) {
        pages.get(index).deleted = false;
    }

    public void rotate(int index, int delta) {
        Page p = pages.get(index);
        p.rotation = ((p.rotation + delta) % 360 + 360) % 360;
    }

    /** 在 pages 列表内上下移动（delta 为 -1 / +1）。返回移动后的下标。 */
    public int move(int index, int delta) {
        int j = index + delta;
        if (j < 0 || j >= pages.size()) {
            return index;
        }
        Page p = pages.remove(index);
        pages.add(j, p);
        return j;
    }

    public void insert(int at, Page p) {
        pages.add(Math.max(0, Math.min(at, pages.size())), p);
    }

    public void setDir(int index, String dir) {
        Page p = pages.get(index);
        p.outName = withDir(p.outName, dir);
    }

    // ------------------------------------------------------------ 保存

    /** 计算保存时各页的最终条目名；renumber=true 时按目录零填充重编号。 */
    public List<String> finalNames(boolean renumber, int digits) {
        List<Page> live = live();
        List<String> names = new ArrayList<String>(live.size());
        java.util.HashMap<String, Integer> counters = new java.util.HashMap<String, Integer>();
        for (Page p : live) {
            if (!renumber) {
                names.add(p.outName);
                continue;
            }
            String base = baseName(p.outName);
            if (base.toLowerCase(Locale.ROOT).startsWith("000_cover")) {
                names.add(p.outName);   // 封面保持原名
                continue;
            }
            String dir = dirOf(p.outName);
            Integer c = counters.get(dir);
            int i = c == null ? 0 : c;
            counters.put(dir, i + 1);
            String num = String.format(Locale.ROOT, "%0" + digits + "d", i);
            names.add(dir.isEmpty() ? num + extOf(p.outName) : dir + "/" + num + extOf(p.outName));
        }
        return names;
    }

    /**
     * 写出新的 cbz。rotated 的页会调用 transcoder 重新编码，其余页原样流式拷贝。
     *
     * @return 写出的页数
     */
    public int save(File out, boolean renumber, int digits, Transcoder transcoder,
                    Progress progress) throws IOException {
        List<Page> live = live();
        List<String> names = finalNames(renumber, digits);
        Set<String> seen = new HashSet<String>();
        for (String n : names) {
            if (!seen.add(n)) {
                throw new IOException("保存后出现重名条目: " + n + "（检查章节目录/重编号设置）");
            }
        }
        ZipOutputStream zos = new ZipOutputStream(new BufferedOutputStream(new FileOutputStream(out)));
        try {
            for (String keep : keepEntries) {
                byte[] data = readEntry(keep);
                if (keep.toLowerCase(Locale.ROOT).endsWith("comicinfo.xml")) {
                    data = patchPageCount(data, live.size());
                }
                zos.setLevel(Deflater.DEFAULT_COMPRESSION);
                put(zos, keep, data);
            }
            int done = 0;
            for (Page p : live) {
                String name = names.get(done);
                if (p.rotation % 360 == 0) {
                    zos.setLevel(Deflater.NO_COMPRESSION);   // 图片已是压缩格式，存储即可
                    if (p.isNew()) {
                        copyFile(zos, name, p.source);
                    } else {
                        copyEntry(zos, name, p.zipName);
                    }
                } else {
                    zos.setLevel(Deflater.NO_COMPRESSION);
                    byte[] rot = transcoder.rotate(read(p), name, p.rotation % 360);
                    put(zos, name, rot);
                }
                done++;
                if (progress != null && (done % 16 == 0 || done == live.size())) {
                    progress.onProgress(done, live.size());
                }
            }
        } finally {
            close(zos);
        }
        return live.size();
    }

    /** 同步更新 ComicInfo.xml 里的 PageCount（纯字符串处理，Android/JVM 通用）。 */
    public static byte[] patchPageCount(byte[] data, int count) {
        String xml;
        try {
            xml = new String(data, "UTF-8");
        } catch (IOException e) {
            return data;
        }
        String out;
        Matcher m = Pattern.compile("<PageCount>\\s*\\d+\\s*</PageCount>").matcher(xml);
        if (m.find()) {
            out = m.replaceFirst("<PageCount>" + count + "</PageCount>");
        } else if (xml.contains("</ComicInfo>")) {
            out = xml.replace("</ComicInfo>", "<PageCount>" + count + "</PageCount></ComicInfo>");
        } else {
            return data;
        }
        try {
            return out.getBytes("UTF-8");
        } catch (IOException e) {
            return data;
        }
    }

    public void close() {
        try {
            if (zip != null) {
                zip.close();
            }
        } catch (IOException ignored) {
        }
        zip = null;
    }

    // ------------------------------------------------------------ 底层工具

    private void put(ZipOutputStream zos, String name, byte[] data) throws IOException {
        ZipEntry e = new ZipEntry(name);
        e.setTime(0L);
        zos.putNextEntry(e);
        zos.write(data);
        zos.closeEntry();
    }

    private void copyEntry(ZipOutputStream zos, String name, String from) throws IOException {
        ZipEntry src = zip.getEntry(from);
        ZipEntry e = new ZipEntry(name);
        e.setTime(0L);
        zos.putNextEntry(e);
        InputStream in = zip.getInputStream(src);
        try {
            pipe(in, zos);
        } finally {
            close(in);
        }
        zos.closeEntry();
    }

    private void copyFile(ZipOutputStream zos, String name, File from) throws IOException {
        ZipEntry e = new ZipEntry(name);
        e.setTime(0L);
        zos.putNextEntry(e);
        InputStream in = new BufferedInputStream(new FileInputStream(from));
        try {
            pipe(in, zos);
        } finally {
            close(in);
        }
        zos.closeEntry();
    }

    private static void pipe(InputStream in, OutputStream out) throws IOException {
        byte[] buf = new byte[256 * 1024];
        int n;
        while ((n = in.read(buf)) > 0) {
            out.write(buf, 0, n);
        }
    }

    private static byte[] readAll(InputStream in) throws IOException {
        ByteArrayOutputStream bos = new ByteArrayOutputStream(256 * 1024);
        pipe(in, bos);
        return bos.toByteArray();
    }

    private static byte[] readFile(File f) throws IOException {
        InputStream in = new BufferedInputStream(new FileInputStream(f));
        try {
            return readAll(in);
        } finally {
            close(in);
        }
    }

    private static void close(java.io.Closeable c) {
        try {
            if (c != null) {
                c.close();
            }
        } catch (IOException ignored) {
        }
    }
}
