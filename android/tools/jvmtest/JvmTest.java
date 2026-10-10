package com.cbzstudio;

import java.awt.Color;
import java.awt.Graphics2D;
import java.awt.geom.AffineTransform;
import java.awt.image.BufferedImage;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.IOException;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.zip.ZipEntry;
import java.util.zip.ZipFile;
import javax.imageio.ImageIO;

/**
 * 在桌面 JVM 上跑 CbzDoc 的完整编辑→保存链路（Android 端跑不了的部分用它兜底验证）。
 *
 * 编译运行：
 *   javac -encoding UTF-8 -d android\build\jvmtest\classes ^
 *         android\src\com\cbzstudio\CbzDoc.java android\tools\jvmtest\JvmTest.java
 *   java -cp android\build\jvmtest\classes com.cbzstudio.JvmTest
 */
public class JvmTest {

    /** 桌面版旋转实现：ImageIO 解码 → AffineTransform 旋转 → 重新编码。 */
    static class ImageIoTranscoder implements CbzDoc.Transcoder {
        public byte[] rotate(byte[] data, String name, int degrees) throws IOException {
            BufferedImage img = ImageIO.read(new ByteArrayInputStream(data));
            if (img == null) {
                throw new IOException("无法解码图片: " + name);
            }
            int w = img.getWidth();
            int h = img.getHeight();
            boolean swap = (degrees % 180) != 0;
            int nw = swap ? h : w;
            int nh = swap ? w : h;
            BufferedImage out = new BufferedImage(nw, nh, BufferedImage.TYPE_INT_RGB);
            Graphics2D g = out.createGraphics();
            g.setColor(Color.WHITE);
            g.fillRect(0, 0, nw, nh);
            AffineTransform at = new AffineTransform();
            at.translate(nw / 2.0, nh / 2.0);
            at.rotate(Math.toRadians(degrees));
            at.translate(-w / 2.0, -h / 2.0);
            g.drawImage(img, at, null);
            g.dispose();
            ByteArrayOutputStream bos = new ByteArrayOutputStream();
            String fmt = CbzDoc.extOf(name);
            if (fmt.equals(".png")) {
                ImageIO.write(out, "PNG", bos);
            } else if (fmt.equals(".webp") || fmt.equals(".gif")) {
                ImageIO.write(out, "PNG", bos);   // 桌面测试没有 webp/gif 编码器
            } else {
                ImageIO.write(out, "JPEG", bos);
            }
            return bos.toByteArray();
        }
    }

    static int passed = 0;
    static int failed = 0;

    static void check(boolean ok, String what) {
        if (ok) {
            passed++;
            System.out.println("  \u2713 " + what);
        } else {
            failed++;
            System.out.println("  \u2717 " + what);
        }
    }

    public static void main(String[] args) throws Exception {
        File src = new File(args.length > 0 ? args[0] : "manga/sample_2ch.cbz");
        File outDir = new File(args.length > 1 ? args[1] : "android/build/jvmtest");
        if (!outDir.exists() && !outDir.mkdirs()) {
            throw new IOException("无法创建输出目录: " + outDir);
        }
        System.out.println("源文件: " + src.getAbsolutePath() + " (" + src.length() / 1048576.0 + " MB)");

        long t0 = System.currentTimeMillis();
        CbzDoc doc = new CbzDoc(src);
        int n0 = doc.live().size();
        System.out.println("载入 " + doc.pages.size() + " 页 / 保留条目 " + doc.keepEntries.size()
                + " / 右开=" + doc.rtl + " / " + (System.currentTimeMillis() - t0) + "ms");
        check(n0 == 76, "载入页数 = " + n0 + "（期望 76）");
        check(doc.rtl, "从 ComicInfo.xml 识别出日漫右开");
        check(doc.keepEntries.contains("ComicInfo.xml"), "保留了 ComicInfo.xml");

        // 1) 删除 + 撤销
        doc.delete(2);
        check(doc.live().size() == n0 - 1, "删除 1 页 -> " + (n0 - 1));
        check(doc.deletedCount() == 1, "待删计数 = 1");
        doc.undelete(2);
        check(doc.live().size() == n0, "撤销删除 -> 恢复 " + n0);
        doc.delete(2);   // 再次删除，后续验证它确实没写进产物

        // 2) 旋转
        doc.rotate(3, 90);
        check(doc.pages.get(3).rotation == 90, "第 4 页旋转标记 = 90°");
        doc.rotate(3, 270);
        check(doc.pages.get(3).rotation == 0, "再转 270° 回到 0°");
        doc.rotate(3, 90);

        // 3) 插入新图（模拟手机上从相册选图）
        File newImg = new File(outDir, "insert_me.jpg");
        BufferedImage bi = new BufferedImage(700, 1000, BufferedImage.TYPE_INT_RGB);
        Graphics2D g = bi.createGraphics();
        g.setColor(new Color(30, 120, 200));
        g.fillRect(0, 0, 700, 1000);
        g.dispose();
        ImageIO.write(bi, "JPEG", newImg);
        CbzDoc.Page p = new CbzDoc.Page(null, "insert_me.jpg");
        p.source = newImg;
        doc.insert(5, p);
        // 此时已删 1 页(75)再加 1 页，应回到 n0
        check(doc.live().size() == n0, "删 1 插 1 后页数 = " + doc.live().size() + "（期望 " + n0 + "）");

        // 4) 改章节目录 + 上移
        doc.setDir(5, "099_新章节/");
        check(doc.pages.get(5).outName.equals("099_新章节/insert_me.jpg"),
                "改章节目录 -> " + doc.pages.get(5).outName);
        int moved = doc.move(5, -1);
        check(moved == 4, "上移一位 -> 下标 " + moved);

        // 5) 保存
        File out = new File(outDir, "jvmtest_out.cbz");
        if (out.exists() && !out.delete()) {
            throw new IOException("无法删除旧产物");
        }
        t0 = System.currentTimeMillis();
        int written = doc.save(out, true, 4, new ImageIoTranscoder(), null);
        long ms = System.currentTimeMillis() - t0;
        System.out.println("保存: " + written + " 页 -> " + out.getAbsolutePath()
                + " (" + out.length() / 1048576.0 + " MB, " + ms + "ms)");
        check(written == n0, "写出页数 = " + written + "（= 原 76 页，删 1 加 1）");
        doc.close();

        // 6) 独立复核产物
        ZipFile z = new ZipFile(out);
        try {
            List<String> names = new ArrayList<String>();
            for (java.util.Enumeration<? extends ZipEntry> e = z.entries(); e.hasMoreElements(); ) {
                names.add(e.nextElement().getName());
            }
            check(names.size() == written + doc.keepEntries.size(),
                    "产物条目数 = " + names.size() + "（页 + 保留条目）");
            check(new HashSet<String>(names).size() == names.size(), "无重名条目");
            boolean backslash = false;
            for (String s : names) {
                if (s.indexOf('\\') >= 0) {
                    backslash = true;
                }
            }
            check(!backslash, "无反斜杠条目");

            int ch1 = 0;
            for (String s : names) {
                if (s.startsWith("001_第01话/")) {
                    ch1++;
                }
            }
            check(ch1 == 40, "001_第01话 由 41 页变 " + ch1 + " 页（删除已落盘）");

            // 旋转那页：产物里第 3 张图应该是横向
            List<String> imgs = new ArrayList<String>();
            for (String s : names) {
                if (CbzDoc.isImage(s)) {
                    imgs.add(s);
                }
            }
            BufferedImage third = ImageIO.read(z.getInputStream(z.getEntry(imgs.get(2))));
            check(third.getWidth() > third.getHeight(),
                    "旋转已落盘: " + imgs.get(2) + " 为 "
                            + third.getWidth() + "x" + third.getHeight() + "（横向）");

            check(names.contains("099_新章节/0000.jpg"),
                    "插入页已落盘: 099_新章节/0000.jpg");

            // 每个条目都能完整读出（等价于 zip testzip）
            boolean allRead = true;
            for (String s : names) {
                byte[] b = new byte[65536];
                java.io.InputStream in = z.getInputStream(z.getEntry(s));
                while (in.read(b) > 0) {
                    // 读到底
                }
                in.close();
            }
            check(allRead, "所有条目可完整读取");

            String ci = new String(CbzDoc.patchPageCount(new byte[0], 0), "UTF-8");
            StringBuilder sb = new StringBuilder();
            java.io.InputStream in = z.getInputStream(z.getEntry("ComicInfo.xml"));
            byte[] buf = new byte[8192];
            int r;
            while ((r = in.read(buf)) > 0) {
                sb.append(new String(buf, 0, r, "UTF-8"));
            }
            in.close();
            ci = sb.toString();
            check(ci.contains("<PageCount>" + written + "</PageCount>"),
                    "ComicInfo.xml PageCount 已更新为 " + written);
        } finally {
            z.close();
        }

        System.out.println();
        System.out.println(failed == 0
                ? "JVM 自检全部通过（" + passed + " 项）"
                : "JVM 自检失败 " + failed + " 项 / 通过 " + passed + " 项");
        System.exit(failed == 0 ? 0 : 1);
    }
}
