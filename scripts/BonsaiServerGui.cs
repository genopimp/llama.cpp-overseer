// QWEN3_STOCK_LLAMA_PATCH 20260922-112453
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Net;
using System.Net.NetworkInformation;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Windows.Forms;

internal static class Native
{
    [DllImport("user32.dll")]
    public static extern bool SetForegroundWindow(IntPtr hWnd);

    [DllImport("user32.dll")]
    public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);

    [DllImport("user32.dll")]
    public static extern bool IsIconic(IntPtr hWnd);

    public const int SW_RESTORE = 9;
}

internal struct GpuSample
{
    public float Util;
    public float Temp;
}

internal sealed class GpuHud : Panel
{
    private const int History = 180;
    private readonly List<GpuSample> samples = new List<GpuSample>();
    private readonly object gate = new object();
    private string error;
    private string gpuName = "GPU";

    public GpuHud()
    {
        DoubleBuffered = true;
        SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.UserPaint | ControlStyles.OptimizedDoubleBuffer, true);
        BackColor = Color.FromArgb(22, 28, 22);
    }

    public void SetName(string name)
    {
        if (string.IsNullOrEmpty(name)) return;
        gpuName = name;
    }

    public void SetError(string message)
    {
        lock (gate) { error = message; }
        if (IsHandleCreated) BeginInvoke(new Action(Invalidate));
    }

    public void AddSample(float util, float temp)
    {
        lock (gate)
        {
            error = null;
            samples.Add(new GpuSample { Util = util, Temp = temp });
            while (samples.Count > History) samples.RemoveAt(0);
        }
        if (IsHandleCreated) BeginInvoke(new Action(Invalidate));
    }

    protected override void OnPaint(PaintEventArgs e)
    {
        base.OnPaint(e);
        var g = e.Graphics;
        g.SmoothingMode = SmoothingMode.AntiAlias;
        g.Clear(BackColor);

        GpuSample[] snap;
        string err;
        lock (gate)
        {
            err = error;
            snap = samples.ToArray();
        }

        using (var border = new Pen(Color.FromArgb(50, 70, 50)))
            g.DrawRectangle(border, 0, 0, Width - 1, Height - 1);

        float util = snap.Length > 0 ? snap[snap.Length - 1].Util : 0;
        float temp = snap.Length > 0 ? snap[snap.Length - 1].Temp : 0;

        int gaugeSize = Math.Min(Height - 16, 150);
        var utilRect = new Rectangle(12, (Height - gaugeSize) / 2, gaugeSize, gaugeSize);
        var tempRect = new Rectangle(12 + gaugeSize + 8, (Height - gaugeSize) / 2, gaugeSize, gaugeSize);
        DrawGauge(g, utilRect, util, 100f, "UTIL", ((int)Math.Round(util)).ToString() + "%", UtilColor(util));
        DrawGauge(g, tempRect, temp, 100f, "TEMP", ((int)Math.Round(temp)).ToString() + " C", TempColor(temp));

        int graphLeft = tempRect.Right + 16;
        int graphTop = 22;
        int graphW = Width - graphLeft - 12;
        int graphH = Height - graphTop - 18;
        if (graphW < 40 || graphH < 40) return;

        var plot = new Rectangle(graphLeft, graphTop, graphW, graphH);
        using (var fill = new SolidBrush(Color.FromArgb(10, 12, 10)))
            g.FillRectangle(fill, plot);
        using (var grid = new Pen(Color.FromArgb(40, 55, 40)))
        {
            for (int i = 0; i <= 4; i++)
            {
                int y = plot.Top + (int)(plot.Height * i / 4.0);
                g.DrawLine(grid, plot.Left, y, plot.Right, y);
            }
        }
        using (var b = new SolidBrush(Color.FromArgb(140, 150, 138)))
        using (var f = new Font("Segoe UI", 7.5f))
        {
            g.DrawString("100", f, b, plot.Right - 24, plot.Top + 1);
            g.DrawString("50", f, b, plot.Right - 18, plot.Top + plot.Height / 2 - 7);
            g.DrawString("0", f, b, plot.Right - 14, plot.Bottom - 12);
            g.DrawString(gpuName + "   ~90s", f, b, plot.Left + 4, 4);
        }
        using (var f = new Font("Segoe UI", 8f, FontStyle.Bold))
        {
            using (var br = new SolidBrush(Color.FromArgb(80, 200, 100)))
                g.DrawString("Util %", f, br, plot.Left + 4, plot.Top + 4);
            using (var br = new SolidBrush(Color.FromArgb(230, 150, 60)))
                g.DrawString("Temp C", f, br, plot.Left + 64, plot.Top + 4);
        }

        if (err != null)
        {
            using (var br = new SolidBrush(Color.FromArgb(200, 80, 70)))
            using (var f = new Font("Segoe UI", 8.5f))
                g.DrawString(err, f, br, plot.Left + 8, plot.Top + 28);
            return;
        }

        DrawSeries(g, plot, snap, true, Color.FromArgb(80, 200, 100));
        DrawSeries(g, plot, snap, false, Color.FromArgb(230, 150, 60));
        using (var border = new Pen(Color.FromArgb(60, 80, 60)))
            g.DrawRectangle(border, plot);
    }

    private static void DrawSeries(Graphics g, Rectangle plot, GpuSample[] snap, bool util, Color color)
    {
        if (snap.Length < 2) return;
        var pts = new PointF[snap.Length];
        float maxN = History - 1;
        for (int i = 0; i < snap.Length; i++)
        {
            float x = plot.Left + (i / maxN) * (plot.Width - 1);
            // right-align so new samples enter from the right
            x = plot.Left + ((i + (History - snap.Length)) / maxN) * (plot.Width - 1);
            float v = util ? snap[i].Util : snap[i].Temp;
            if (v < 0) v = 0;
            if (v > 100) v = 100;
            float y = plot.Bottom - 1 - (v / 100f) * (plot.Height - 2);
            pts[i] = new PointF(x, y);
        }
        using (var pen = new Pen(color, 1.8f) { LineJoin = LineJoin.Round })
            g.DrawLines(pen, pts);
    }

    private static void DrawGauge(Graphics g, Rectangle r, float value, float max, string caption, string readout, Color color)
    {
        int pad = 14;
        var arc = new Rectangle(r.X + pad, r.Y + pad, r.Width - pad * 2, r.Height - pad * 2);
        const float start = 135f;
        const float sweep = 270f;
        float pct = max <= 0 ? 0 : Math.Max(0, Math.Min(1f, value / max));
        using (var bg = new Pen(Color.FromArgb(45, 58, 45), 10) { StartCap = LineCap.Round, EndCap = LineCap.Round })
            g.DrawArc(bg, arc, start, sweep);
        if (pct > 0.002f)
        {
            using (var fg = new Pen(color, 10) { StartCap = LineCap.Round, EndCap = LineCap.Round })
                g.DrawArc(fg, arc, start, sweep * pct);
        }
        using (var fVal = new Font("Segoe UI", 12f, FontStyle.Bold))
        using (var fCap = new Font("Segoe UI", 7.5f, FontStyle.Bold))
        using (var brVal = new SolidBrush(Color.FromArgb(232, 236, 230)))
        using (var brCap = new SolidBrush(Color.FromArgb(150, 160, 148)))
        {
            var sz = g.MeasureString(readout, fVal);
            g.DrawString(readout, fVal, brVal, r.X + (r.Width - sz.Width) / 2, r.Y + (r.Height - sz.Height) / 2 - 4);
            var cz = g.MeasureString(caption, fCap);
            g.DrawString(caption, fCap, brCap, r.X + (r.Width - cz.Width) / 2, r.Y + (r.Height - cz.Height) / 2 + 16);
        }
    }

    private static Color UtilColor(float util)
    {
        if (util >= 85) return Color.FromArgb(80, 200, 100);
        if (util >= 40) return Color.FromArgb(160, 200, 80);
        return Color.FromArgb(120, 160, 120);
    }

    private static Color TempColor(float temp)
    {
        if (temp >= 80) return Color.FromArgb(220, 80, 70);
        if (temp >= 65) return Color.FromArgb(230, 150, 60);
        return Color.FromArgb(80, 190, 140);
    }
}

internal sealed class ContextHud : Panel
{
    private int ctx;
    private int used;
    private int prompt;
    private int generated;
    private bool processing;
    private string note = "Context  waiting for server";

    public ContextHud()
    {
        DoubleBuffered = true;
        SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.UserPaint | ControlStyles.OptimizedDoubleBuffer, true);
        BackColor = Color.FromArgb(22, 28, 22);
    }

    public void SetIdle()
    {
        ctx = used = prompt = generated = 0;
        processing = false;
        note = "Context  server not running";
        if (IsHandleCreated) BeginInvoke(new Action(Invalidate));
    }

    public void SetValues(int nCtx, int nUsed, int nPrompt, int nGen, bool busy)
    {
        ctx = nCtx;
        used = nUsed;
        prompt = nPrompt;
        generated = nGen;
        processing = busy;
        if (nCtx <= 0)
            note = "Context  -";
        else
        {
            int pct = (int)Math.Round(100.0 * nUsed / nCtx);
            int free = Math.Max(0, nCtx - nUsed);
            string state = busy ? "generating" : "idle";
            note = string.Format("Context  {0:n0} / {1:n0}  ({2}%)    prompt {3:n0}   gen {4:n0}   free {5:n0}    {6}",
                nUsed, nCtx, pct, nPrompt, nGen, free, state);
            if (pct >= 85) note += "    HIGH  compact or new chat";
            else if (pct >= 70) note += "    approaching limit";
        }
        if (IsHandleCreated) BeginInvoke(new Action(Invalidate));
        else Invalidate();
    }

    protected override void OnPaint(PaintEventArgs e)
    {
        base.OnPaint(e);
        var g = e.Graphics;
        g.SmoothingMode = SmoothingMode.AntiAlias;
        g.Clear(BackColor);
        using (var border = new Pen(Color.FromArgb(50, 70, 50)))
            g.DrawRectangle(border, 0, 0, Width - 1, Height - 1);

        var bar = new Rectangle(12, 28, Math.Max(10, Width - 24), 16);
        using (var fill = new SolidBrush(Color.FromArgb(10, 12, 10)))
            g.FillRectangle(fill, bar);

        float frac = ctx > 0 ? Math.Min(1f, (float)used / ctx) : 0f;
        float pFrac = ctx > 0 ? Math.Min(frac, (float)prompt / ctx) : 0f;
        float gFrac = Math.Max(0f, frac - pFrac);
        Color edge = frac >= 0.85f ? Color.FromArgb(220, 80, 70)
            : frac >= 0.70f ? Color.FromArgb(230, 150, 60)
            : Color.FromArgb(80, 180, 90);

        if (pFrac > 0)
        {
            using (var br = new SolidBrush(Color.FromArgb(50, 110, 160)))
                g.FillRectangle(br, bar.X, bar.Y, (int)(bar.Width * pFrac), bar.Height);
        }
        if (gFrac > 0)
        {
            int x = bar.X + (int)(bar.Width * pFrac);
            using (var br = new SolidBrush(edge))
                g.FillRectangle(br, x, bar.Y, Math.Max(1, (int)(bar.Width * gFrac)), bar.Height);
        }
        using (var pen = new Pen(Color.FromArgb(60, 80, 60)))
            g.DrawRectangle(pen, bar);

        using (var f = new Font("Segoe UI", 8.5f, FontStyle.Bold))
        using (var br = new SolidBrush(frac >= 0.85f ? Color.FromArgb(230, 140, 130)
            : frac >= 0.70f ? Color.FromArgb(230, 180, 90)
            : Color.FromArgb(200, 210, 198)))
        {
            g.DrawString(note, f, br, 12, 6);
        }

        using (var f = new Font("Segoe UI", 7.5f))
        using (var br = new SolidBrush(Color.FromArgb(140, 150, 138)))
        {
            g.DrawString("blue = prompt already in the window     colored = this turn's generation", f, br, 12, Height - 18);
        }
    }
}

internal sealed class DialogueForm : Form
{
    private readonly string jobDir;
    private readonly RichTextBox logBox;
    private readonly TextBox input;
    private readonly Button send;
    private readonly Label banner;
    private readonly System.Windows.Forms.Timer timer;
    private long logPos;
    private string lastBanner = "";

    public DialogueForm(string jobDir)
    {
        this.jobDir = jobDir;
        Text = "Overseer dialogue";
        FormBorderStyle = FormBorderStyle.SizableToolWindow;
        StartPosition = FormStartPosition.Manual;
        Size = new Size(440, 640);
        MinimumSize = new Size(360, 400);
        BackColor = Color.FromArgb(16, 20, 16);
        ForeColor = Color.FromArgb(232, 236, 230);
        Font = new Font("Segoe UI", 9.5f);

        banner = new Label
        {
            Location = new Point(10, 8),
            Size = new Size(400, 36),
            ForeColor = Color.FromArgb(230, 180, 90),
            Text = "Live overseer / worker log. Type below to answer questions or give guidance."
        };
        logBox = new RichTextBox
        {
            Location = new Point(10, 48),
            Size = new Size(400, 480),
            ReadOnly = true,
            BackColor = Color.FromArgb(10, 12, 10),
            ForeColor = Color.FromArgb(200, 210, 198),
            BorderStyle = BorderStyle.FixedSingle,
            Font = new Font("Consolas", 9f),
            DetectUrls = false,
            Anchor = AnchorStyles.Top | AnchorStyles.Bottom | AnchorStyles.Left | AnchorStyles.Right
        };
        input = new TextBox
        {
            Location = new Point(10, 536),
            Size = new Size(300, 28),
            BackColor = Color.FromArgb(28, 34, 28),
            ForeColor = Color.FromArgb(232, 236, 230),
            BorderStyle = BorderStyle.FixedSingle,
            Anchor = AnchorStyles.Bottom | AnchorStyles.Left | AnchorStyles.Right
        };
        send = new Button
        {
            Text = "Send",
            Location = new Point(318, 532),
            Size = new Size(92, 32),
            FlatStyle = FlatStyle.Flat,
            BackColor = Color.FromArgb(62, 140, 72),
            ForeColor = Color.FromArgb(232, 236, 230),
            Anchor = AnchorStyles.Bottom | AnchorStyles.Right
        };
        send.FlatAppearance.BorderSize = 0;
        send.Click += delegate { Send(); };
        input.KeyDown += (s, e) =>
        {
            if (e.KeyCode == Keys.Enter && !e.Shift)
            {
                e.SuppressKeyPress = true;
                Send();
            }
        };
        Controls.Add(banner);
        Controls.Add(logBox);
        Controls.Add(input);
        Controls.Add(send);
        Resize += delegate
        {
            banner.Width = ClientSize.Width - 20;
            logBox.Width = ClientSize.Width - 20;
            logBox.Height = Math.Max(80, ClientSize.Height - 108);
            input.Top = ClientSize.Height - 44;
            input.Width = Math.Max(80, ClientSize.Width - 124);
            send.Top = ClientSize.Height - 48;
            send.Left = ClientSize.Width - 102;
        };
        timer = new System.Windows.Forms.Timer { Interval = 400 };
        timer.Tick += delegate { Poll(); };
        Shown += delegate { Poll(); timer.Start(); };
        FormClosed += delegate { timer.Stop(); };
    }

    private void Send()
    {
        var text = (input.Text ?? "").Trim();
        if (text.Length == 0) return;
        Directory.CreateDirectory(jobDir);
        var rec = "{\"ts\":\"" + DateTime.UtcNow.ToString("o") + "\",\"text\":" + JsonEscape(text) + "}\n";
        File.AppendAllText(Path.Combine(jobDir, "inbox.jsonl"), rec, Encoding.UTF8);
        input.Clear();
        AppendLine("you", DateTime.Now.ToString("HH:mm:ss"), text);
    }

    private static string JsonEscape(string s)
    {
        return "\"" + s.Replace("\\", "\\\\").Replace("\"", "\\\"").Replace("\r", "\\r").Replace("\n", "\\n") + "\"";
    }

    private void Poll()
    {
        try
        {
            var qpath = Path.Combine(jobDir, "pending_question.json");
            if (File.Exists(qpath))
            {
                var q = File.ReadAllText(qpath);
                var question = ExtractJsonString(q, "question");
                var b = string.IsNullOrEmpty(question) ? "Overseer is waiting for you." : ("Overseer asks: " + question);
                if (b != lastBanner)
                {
                    lastBanner = b;
                    banner.Text = b;
                    banner.ForeColor = Color.FromArgb(230, 180, 90);
                }
            }
            else if (lastBanner != "")
            {
                lastBanner = "";
                banner.Text = "Live overseer / worker log. Type below to answer questions or give guidance.";
                banner.ForeColor = Color.FromArgb(150, 160, 148);
            }

            var path = Path.Combine(jobDir, "dialogue.jsonl");
            if (!File.Exists(path)) return;
            using (var fs = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
            {
                if (fs.Length < logPos) logPos = 0;
                if (fs.Length == logPos) return;
                fs.Seek(logPos, SeekOrigin.Begin);
                var buf = new byte[fs.Length - logPos];
                int n = fs.Read(buf, 0, buf.Length);
                logPos = fs.Position;
                var chunk = Encoding.UTF8.GetString(buf, 0, n);
                foreach (var line in chunk.Split('\n'))
                {
                    var t = line.Trim();
                    if (t.Length == 0) continue;
                    var role = ExtractJsonString(t, "role");
                    var text = ExtractJsonString(t, "text");
                    var ts = ExtractJsonString(t, "ts");
                    if (text.Length == 0) continue;
                    if (ts.Length >= 19) ts = ts.Substring(11, 8);
                    AppendLine(role, ts, text);
                }
            }
        }
        catch { }
    }

    private void AppendLine(string role, string ts, string text)
    {
        Color c;
        if (role == "overseer") c = Color.FromArgb(120, 200, 130);
        else if (role == "worker") c = Color.FromArgb(140, 180, 210);
        else if (role == "user" || role == "you") c = Color.FromArgb(232, 236, 230);
        else c = Color.FromArgb(210, 160, 60);
        logBox.SelectionStart = logBox.TextLength;
        logBox.SelectionColor = Color.FromArgb(110, 120, 110);
        logBox.AppendText((ts.Length > 0 ? ts : "--:--:--") + "  ");
        logBox.SelectionColor = c;
        logBox.AppendText(role.ToUpper() + "\n");
        logBox.SelectionColor = c;
        logBox.AppendText(text.Replace("\n", "\n  ") + "\n\n");
        logBox.SelectionStart = logBox.TextLength;
        logBox.ScrollToCaret();
    }

    private static string ExtractJsonString(string json, string key)
    {
        var needle = "\"" + key + "\":";
        var i = json.IndexOf(needle, StringComparison.Ordinal);
        if (i < 0) return "";
        i += needle.Length;
        while (i < json.Length && json[i] == ' ') i++;
        if (i >= json.Length || json[i] != '"') return "";
        i++;
        var sb = new StringBuilder();
        while (i < json.Length)
        {
            char ch = json[i++];
            if (ch == '\\' && i < json.Length)
            {
                char n = json[i++];
                if (n == 'n') sb.Append('\n');
                else if (n == 't') sb.Append('\t');
                else sb.Append(n);
            }
            else if (ch == '"') break;
            else sb.Append(ch);
        }
        return sb.ToString();
    }
}

internal sealed class CodeViewForm : Form
{
    private static readonly string[] SourceExt = { ".cs", ".csproj", ".sln", ".json", ".md", ".py", ".xml", ".xaml", ".resx", ".txt", ".hlsl" };
    private readonly string jobDir;
    private readonly string root;
    private readonly ListBox files;
    private readonly TextBox code;
    private readonly Label banner;
    private readonly System.Windows.Forms.Timer timer;
    private FileSystemWatcher watcher;
    private long logPos = -1;
    private DateTime watchSince = DateTime.MinValue;
    private readonly List<string> paths = new List<string>();

    public CodeViewForm(string jobDir, string root)
    {
        this.jobDir = jobDir;
        this.root = root;
        Text = "Generated code";
        FormBorderStyle = FormBorderStyle.SizableToolWindow;
        StartPosition = FormStartPosition.Manual;
        Size = new Size(720, 620);
        MinimumSize = new Size(420, 360);
        BackColor = Color.FromArgb(16, 20, 16);
        ForeColor = Color.FromArgb(232, 236, 230);
        Font = new Font("Segoe UI", 9.5f);

        banner = new Label
        {
            Location = new Point(10, 8),
            Size = new Size(690, 22),
            ForeColor = Color.FromArgb(150, 160, 148),
            Text = "Watching workspace for source files…"
        };
        files = new ListBox
        {
            Location = new Point(10, 34),
            Size = new Size(220, 530),
            BackColor = Color.FromArgb(10, 12, 10),
            ForeColor = Color.FromArgb(190, 220, 180),
            BorderStyle = BorderStyle.FixedSingle,
            Anchor = AnchorStyles.Top | AnchorStyles.Bottom | AnchorStyles.Left
        };
        files.SelectedIndexChanged += delegate { ShowSelected(); };
        code = new TextBox
        {
            Location = new Point(236, 34),
            Size = new Size(460, 530),
            Multiline = true,
            ReadOnly = true,
            ScrollBars = ScrollBars.Both,
            WordWrap = false,
            BackColor = Color.FromArgb(10, 12, 10),
            ForeColor = Color.FromArgb(210, 230, 200),
            BorderStyle = BorderStyle.FixedSingle,
            Font = new Font("Consolas", 9f),
            Anchor = AnchorStyles.Top | AnchorStyles.Bottom | AnchorStyles.Left | AnchorStyles.Right
        };
        Controls.Add(banner);
        Controls.Add(files);
        Controls.Add(code);
        Resize += delegate
        {
            banner.Width = ClientSize.Width - 20;
            files.Height = Math.Max(80, ClientSize.Height - 48);
            code.Width = Math.Max(80, ClientSize.Width - 254);
            code.Height = files.Height;
        };
        timer = new System.Windows.Forms.Timer { Interval = 500 };
        timer.Tick += delegate { PollJsonl(); ScanDisk(); };
        Shown += delegate
        {
            StartWatcher();
            PollJsonl();
            if (paths.Count == 0) { watchSince = DateTime.UtcNow.AddHours(-12); ScanDisk(); }
            timer.Start();
        };
        FormClosed += delegate
        {
            timer.Stop();
            if (watcher != null) { watcher.EnableRaisingEvents = false; watcher.Dispose(); }
        };
    }

    private void StartWatcher()
    {
        try
        {
            if (!Directory.Exists(root)) return;
            watcher = new FileSystemWatcher(root)
            {
                IncludeSubdirectories = true,
                NotifyFilter = NotifyFilters.LastWrite | NotifyFilters.FileName | NotifyFilters.CreationTime,
                Filter = "*.*"
            };
            watcher.Created += OnFs;
            watcher.Changed += OnFs;
            watcher.Renamed += OnFs;
            watcher.EnableRaisingEvents = true;
        }
        catch (Exception ex) { banner.Text = "Watch failed: " + ex.Message; }
    }

    private void OnFs(object sender, FileSystemEventArgs e)
    {
        if (IsDisposed) return;
        try { BeginInvoke(new Action(() => AddRel(RelFromFull(e.FullPath), true))); }
        catch { }
    }

    private void HardReset(string why)
    {
        paths.Clear();
        files.Items.Clear();
        code.Clear();
        banner.Text = why;
    }

    private void PollJsonl()
    {
        try
        {
            var path = Path.Combine(jobDir, "codeview.jsonl");
            if (!File.Exists(path)) return;
            var lines = File.ReadAllLines(path);
            if (logPos < 0)
            {
                int start = 0;
                for (int i = 0; i < lines.Length; i++)
                    if (Extract(lines[i], "event") == "reset") start = i;
                for (int i = start; i < lines.Length; i++) ApplyLine(lines[i]);
                logPos = lines.Length;
                return;
            }
            if (lines.Length < logPos) logPos = 0;
            for (int i = (int)logPos; i < lines.Length; i++) ApplyLine(lines[i]);
            logPos = lines.Length;
        }
        catch (Exception ex) { banner.Text = "codeview: " + ex.Message; }
    }

    private void ApplyLine(string line)
    {
        var t = (line ?? "").Trim();
        if (t.Length == 0) return;
        var ev = Extract(t, "event");
        if (ev == "reset")
        {
            var title = Extract(t, "title");
            var task = Extract(t, "task");
            var ts = Extract(t, "ts");
            DateTime parsed;
            if (DateTime.TryParse(ts, null, System.Globalization.DateTimeStyles.AdjustToUniversal, out parsed))
                watchSince = parsed.ToUniversalTime().AddSeconds(-1);
            else
                watchSince = DateTime.UtcNow.AddSeconds(-2);
            var why = string.IsNullOrEmpty(task) ? "New job — watching for new source…" : ("Task " + task + " — " + title);
            HardReset(why);
            if (!string.IsNullOrEmpty(task)) Text = "Generated code — " + task;
            ScanDisk();
        }
        else if (ev == "file")
        {
            var rel = Extract(t, "path");
            AddRel(rel, true);
        }
    }

    private void ScanDisk()
    {
        try
        {
            if (!Directory.Exists(root)) return;
            foreach (var full in Directory.EnumerateFiles(root, "*.*", SearchOption.AllDirectories))
            {
                if (!IsSource(full)) continue;
                var fi = new FileInfo(full);
                if (watchSince != DateTime.MinValue && fi.LastWriteTimeUtc < watchSince) continue;
                AddRel(RelFromFull(full), false);
            }
            if (files.SelectedIndex < 0 && files.Items.Count > 0)
            {
                files.SelectedIndex = files.Items.Count - 1;
                ShowSelected();
            }
            if (paths.Count == 0)
                banner.Text = "No source files changed yet this task (watching " + root + ")";
        }
        catch (Exception ex) { banner.Text = "scan: " + ex.Message; }
    }

    private void AddRel(string rel, bool select)
    {
        if (string.IsNullOrEmpty(rel)) return;
        rel = rel.Replace('/', Path.DirectorySeparatorChar).TrimStart(Path.DirectorySeparatorChar);
        var full = Path.Combine(root, rel);
        if (!IsSource(full)) return;
        if (!paths.Contains(rel))
        {
            paths.Add(rel);
            files.Items.Add(rel);
        }
        if (select)
        {
            files.SelectedItem = rel;
            ShowSelected();
            banner.Text = "Updated  " + rel;
        }
    }

    private bool IsSource(string full)
    {
        if (string.IsNullOrEmpty(full)) return false;
        var n = full.Replace('/', '\\').ToLowerInvariant();
        if (n.Contains("\\bin\\") || n.Contains("\\obj\\") || n.Contains("\\.overseer\\") || n.Contains("\\.git\\") || n.Contains("\\.vs\\"))
            return false;
        var ext = Path.GetExtension(full).ToLowerInvariant();
        foreach (var e in SourceExt) if (ext == e) return true;
        return false;
    }

    private string RelFromFull(string full)
    {
        try
        {
            var r = Path.GetFullPath(root).TrimEnd('\\') + "\\";
            var f = Path.GetFullPath(full);
            if (f.StartsWith(r, StringComparison.OrdinalIgnoreCase))
                return f.Substring(r.Length);
        }
        catch { }
        return "";
    }

    private void ShowSelected()
    {
        if (files.SelectedItem == null) return;
        var rel = files.SelectedItem.ToString();
        var full = Path.Combine(root, rel);
        if (!File.Exists(full))
        {
            code.Text = "// missing: " + rel;
            return;
        }
        try
        {
            var fi = new FileInfo(full);
            var text = File.ReadAllText(full, Encoding.UTF8);
            if (fi.Length > 400000) text = text.Substring(0, 400000) + "\r\n// truncated";
            code.Text = text;
        }
        catch (Exception ex) { code.Text = "// " + ex.Message; }
    }

    private static string Extract(string json, string key)
    {
        var needle = "\"" + key + "\":";
        var i = json.IndexOf(needle, StringComparison.Ordinal);
        if (i < 0) return "";
        i += needle.Length;
        while (i < json.Length && (json[i] == ' ' || json[i] == '\t')) i++;
        if (i >= json.Length || json[i] != '"') return "";
        i++;
        var sb = new StringBuilder();
        while (i < json.Length)
        {
            char ch = json[i++];
            if (ch == '\\' && i < json.Length)
            {
                char n = json[i++];
                sb.Append(n == 'n' ? '\n' : n);
            }
            else if (ch == '"') break;
            else sb.Append(ch);
        }
        return sb.ToString();
    }
}

internal sealed class LiveLlmForm : Form
{
    private readonly string jobDir;
    private readonly RichTextBox box;
    private readonly System.Windows.Forms.Timer timer;
    private long pos = -1;

    public LiveLlmForm(string jobDir)
    {
        this.jobDir = jobDir;
        Text = "Live LLM  —  overseer / worker / tools";
        Size = new Size(560, 720);
        StartPosition = FormStartPosition.Manual;
        FormBorderStyle = FormBorderStyle.SizableToolWindow;
        BackColor = Color.FromArgb(14, 18, 14);
        box = new RichTextBox
        {
            Dock = DockStyle.Fill,
            ReadOnly = true,
            BackColor = Color.FromArgb(10, 12, 10),
            ForeColor = Color.FromArgb(200, 210, 198),
            Font = new Font("Consolas", 9f),
            DetectUrls = false,
            WordWrap = true
        };
        Controls.Add(box);
        timer = new System.Windows.Forms.Timer { Interval = 500 };
        timer.Tick += delegate { Poll(); };
        Shown += delegate { Poll(); timer.Start(); };
        FormClosed += delegate { timer.Stop(); };
    }

    private void Poll()
    {
        try
        {
            var path = Path.Combine(jobDir, "llm_trace.jsonl");
            if (!File.Exists(path)) return;
            var lines = File.ReadAllLines(path);
            int start = pos < 0 ? Math.Max(0, lines.Length - 80) : (int)pos;
            if (start > lines.Length) start = 0;
            for (int i = start; i < lines.Length; i++) Append(lines[i]);
            pos = lines.Length;
        }
        catch { }
    }

    private void Append(string line)
    {
        line = (line ?? "").Trim();
        if (line.Length == 0) return;
        string agent = Extract(line, "agent");
        string task = Extract(line, "task");
        string tool = Extract(line, "tool");
        string prompt = Extract(line, "prompt");
        string resp = Extract(line, "response");
        string think = Extract(line, "thinking");
        Color c = Color.FromArgb(180, 180, 180);
        if (agent == "overseer") c = Color.FromArgb(90, 200, 110);
        else if (agent == "worker") c = Color.FromArgb(110, 170, 220);
        else if (agent == "tool") c = Color.FromArgb(210, 170, 70);
        string who = agent.ToUpper();
        if (task.Length > 0) who += " " + task;
        if (tool.Length > 0) who += "  [" + tool + "]";
        box.SelectionStart = box.TextLength;
        box.SelectionColor = c;
        box.AppendText(who + "\n");
        if (prompt.Length > 0)
        {
            box.SelectionColor = Color.FromArgb(140, 150, 140);
            box.AppendText("▸ " + Trim(prompt, 900) + "\n");
        }
        if (think.Length > 0)
        {
            box.SelectionColor = Color.FromArgb(130, 140, 120);
            box.AppendText(Trim(think, 500) + "\n");
        }
        if (resp.Length > 0)
        {
            box.SelectionColor = c;
            box.AppendText(Trim(resp, 1200) + "\n");
        }
        box.AppendText("\n");
        box.SelectionStart = box.TextLength;
        box.ScrollToCaret();
    }

    private static string Trim(string s, int n)
    {
        s = s.Replace("\\n", "\n");
        return s.Length <= n ? s : s.Substring(0, n) + "…";
    }

    private static string Extract(string json, string key)
    {
        var needle = "\"" + key + "\":";
        var i = json.IndexOf(needle, StringComparison.Ordinal);
        if (i < 0) return "";
        i += needle.Length;
        while (i < json.Length && (json[i] == ' ' || json[i] == '\t')) i++;
        if (i >= json.Length || json[i] != '"') return "";
        i++;
        var sb = new StringBuilder();
        while (i < json.Length)
        {
            char ch = json[i++];
            if (ch == '\\' && i < json.Length)
            {
                char n = json[i++];
                if (n == 'n') sb.Append('\n');
                else sb.Append(n);
            }
            else if (ch == '"') break;
            else sb.Append(ch);
        }
        return sb.ToString();
    }
}


internal sealed class OpsLiveForm : Form
{
    private readonly string jobDir;
    private readonly string demoDir;
    private readonly Label strip;
    private readonly TextBox orch;
    private readonly TextBox promptBox;
    private readonly TextBox responseBox;
    private readonly TextBox diffBox;
    private readonly TextBox inbox;
    private readonly Button btnSend;
    private readonly Button btnClear;
    private readonly Button btnRefresh;
    private readonly System.Windows.Forms.Timer timer;
    private string lastOrch = "";
    private string lastPrompt = "";
    private string lastResp = "";
    private string lastDiff = "";
    private DateTime lastActivityUtc = DateTime.MinValue;

    public OpsLiveForm(string jobDir, string demoDir)
    {
        this.jobDir = jobDir;
        this.demoDir = demoDir;
        Text = "Ops Live  -  orch / prompt / response / diffs";
        Size = new Size(980, 860);
        MinimumSize = new Size(820, 640);
        StartPosition = FormStartPosition.Manual;
        FormBorderStyle = FormBorderStyle.SizableToolWindow;
        BackColor = Color.FromArgb(14, 18, 14);
        ForeColor = Color.FromArgb(220, 230, 218);

        strip = new Label
        {
            Dock = DockStyle.Top,
            Height = 36,
            TextAlign = ContentAlignment.MiddleLeft,
            Padding = new Padding(10, 0, 0, 0),
            Font = new Font("Consolas", 9f),
            BackColor = Color.FromArgb(22, 28, 22),
            ForeColor = Color.FromArgb(180, 220, 170),
            Text = "CPU -   GPU -   Llama -   Overseer -   Stall -"
        };
        Controls.Add(strip);

        var bottom = new Panel { Dock = DockStyle.Bottom, Height = 64, BackColor = Color.FromArgb(18, 22, 18) };
        inbox = new TextBox
        {
            Location = new Point(10, 18),
            Size = new Size(620, 28),
            Anchor = AnchorStyles.Left | AnchorStyles.Right | AnchorStyles.Top,
            BackColor = Color.FromArgb(10, 12, 10),
            ForeColor = Color.FromArgb(220, 230, 218),
            BorderStyle = BorderStyle.FixedSingle,
            Font = new Font("Segoe UI", 10f)
        };
        btnSend = MakeBtn("Send inbox", 640, 16, 100);
        btnRefresh = MakeBtn("Refresh", 748, 16, 90);
        btnClear = MakeBtn("Clear panes", 846, 16, 110);
        btnSend.Anchor = AnchorStyles.Top | AnchorStyles.Right;
        btnRefresh.Anchor = AnchorStyles.Top | AnchorStyles.Right;
        btnClear.Anchor = AnchorStyles.Top | AnchorStyles.Right;
        bottom.Controls.Add(inbox);
        bottom.Controls.Add(btnSend);
        bottom.Controls.Add(btnRefresh);
        bottom.Controls.Add(btnClear);
        bottom.Resize += delegate
        {
            inbox.Width = Math.Max(200, bottom.ClientSize.Width - 340);
            btnSend.Left = inbox.Right + 10;
            btnRefresh.Left = btnSend.Right + 8;
            btnClear.Left = btnRefresh.Right + 8;
        };
        Controls.Add(bottom);

        var grid = new TableLayoutPanel
        {
            Dock = DockStyle.Fill,
            ColumnCount = 2,
            RowCount = 2,
            Padding = new Padding(8),
            BackColor = Color.FromArgb(14, 18, 14)
        };
        grid.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 50));
        grid.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 50));
        grid.RowStyles.Add(new RowStyle(SizeType.Percent, 50));
        grid.RowStyles.Add(new RowStyle(SizeType.Percent, 50));

        orch = MakePane();
        promptBox = MakePane();
        responseBox = MakePane();
        diffBox = MakePane();
        grid.Controls.Add(Wrap("Orchestration / status", orch), 0, 0);
        grid.Controls.Add(Wrap("Prompt", promptBox), 1, 0);
        grid.Controls.Add(Wrap("Response", responseBox), 0, 1);
        grid.Controls.Add(Wrap("Code diffs / writes", diffBox), 1, 1);
        Controls.Add(grid);
        grid.BringToFront();

        btnClear.Click += delegate
        {
            orch.Clear(); promptBox.Clear(); responseBox.Clear(); diffBox.Clear();
            lastOrch = lastPrompt = lastResp = lastDiff = "";
        };
        btnRefresh.Click += delegate { Poll(true); };
        btnSend.Click += delegate { SendInbox(); };
        inbox.KeyDown += (s, e) =>
        {
            if (e.KeyCode == Keys.Enter && !e.Shift)
            {
                e.SuppressKeyPress = true;
                SendInbox();
            }
        };

        timer = new System.Windows.Forms.Timer { Interval = 1500 };
        timer.Tick += delegate { Poll(false); };
        Shown += delegate { Poll(true); timer.Start(); };
        FormClosed += delegate { timer.Stop(); };
    }

    private static Button MakeBtn(string text, int x, int y, int w)
    {
        return new Button
        {
            Text = text,
            Location = new Point(x, y),
            Size = new Size(w, 32),
            FlatStyle = FlatStyle.Flat,
            BackColor = Color.FromArgb(42, 52, 42),
            ForeColor = Color.FromArgb(220, 230, 218),
            Font = new Font("Segoe UI", 9f),
            Cursor = Cursors.Hand
        };
    }

    private static TextBox MakePane()
    {
        return new TextBox
        {
            Multiline = true,
            ReadOnly = true,
            ScrollBars = ScrollBars.Both,
            WordWrap = false,
            Dock = DockStyle.Fill,
            BackColor = Color.FromArgb(10, 12, 10),
            ForeColor = Color.FromArgb(200, 215, 198),
            Font = new Font("Consolas", 9f),
            BorderStyle = BorderStyle.FixedSingle
        };
    }

    private static Control Wrap(string title, Control body)
    {
        var p = new Panel { Dock = DockStyle.Fill, Padding = new Padding(4) };
        var lbl = new Label
        {
            Text = title,
            Dock = DockStyle.Top,
            Height = 22,
            ForeColor = Color.FromArgb(150, 170, 150),
            Font = new Font("Segoe UI", 8.5f, FontStyle.Bold)
        };
        p.Controls.Add(body);
        p.Controls.Add(lbl);
        return p;
    }

    private void SendInbox()
    {
        var text = (inbox.Text ?? "").Trim();
        if (text.Length == 0) return;
        try
        {
            Directory.CreateDirectory(jobDir);
            var tok = "";
            var tf = Path.Combine(jobDir, "bridge_token.txt");
            if (File.Exists(tf)) tok = File.ReadAllText(tf).Trim();
            bool posted = false;
            if (tok.Length > 0)
            {
                try
                {
                    var req = (HttpWebRequest)WebRequest.Create("http://127.0.0.1:8787/api/inbox?token=" + Uri.EscapeDataString(tok));
                    req.Method = "POST";
                    req.ContentType = "application/json";
                    req.Timeout = 3000;
                    var payload = Encoding.UTF8.GetBytes("{\"text\":" + JsonQuote(text) + "}");
                    req.ContentLength = payload.Length;
                    using (var s = req.GetRequestStream()) s.Write(payload, 0, payload.Length);
                    using (var resp = (HttpWebResponse)req.GetResponse())
                        posted = resp.StatusCode == HttpStatusCode.OK;
                }
                catch { posted = false; }
            }
            if (!posted)
            {
                // Fallback: append to inbox.jsonl for overseer take_inbox
                var line = "{\"text\":" + JsonQuote(text) + ",\"ts\":\"" + DateTime.UtcNow.ToString("o") + "\"}\n";
                File.AppendAllText(Path.Combine(jobDir, "inbox.jsonl"), line, Encoding.UTF8);
            }
            inbox.Clear();
            lastActivityUtc = DateTime.UtcNow;
            AppendUnique(orch, "[inbox] " + text + "\n");
        }
        catch (Exception ex)
        {
            MessageBox.Show(ex.Message, "Send inbox", MessageBoxButtons.OK, MessageBoxIcon.Warning);
        }
    }

    private static string JsonQuote(string s)
    {
        if (s == null) s = "";
        var sb = new StringBuilder("\"");
        foreach (char c in s)
        {
            if (c == '\\' || c == '"') sb.Append('\\').Append(c);
            else if (c == '\n') sb.Append("\\n");
            else if (c == '\r') sb.Append("\\r");
            else if (c == '\t') sb.Append("\\t");
            else sb.Append(c);
        }
        sb.Append('"');
        return sb.ToString();
    }

    private void Poll(bool force)
    {
        try { UpdateStrip(); } catch { }
        try { UpdateOrch(force); } catch { }
        try { UpdateTrace(force); } catch { }
        try { UpdateDiffs(force); } catch { }
    }

    private void UpdateStrip()
    {
        float cpu = ReadCpu();
        string gpu = ReadGpu();
        string llama = Probe("http://127.0.0.1:8080/health") ? "up" : "down";
        string ov = "idle";
        string stall = "ok";
        try
        {
            var sp = Path.Combine(jobDir, "state.json");
            if (File.Exists(sp))
            {
                var js = File.ReadAllText(sp);
                ov = ExtractJson(js, "status");
                if (ov.Length == 0) ov = "running?";
                var mtime = File.GetLastWriteTimeUtc(sp);
                if (mtime > lastActivityUtc) lastActivityUtc = mtime;
            }
            foreach (var name in new[] { "llm_trace.jsonl", "overseer.log", "dialogue.jsonl" })
            {
                var p = Path.Combine(jobDir, name);
                if (File.Exists(p))
                {
                    var m = File.GetLastWriteTimeUtc(p);
                    if (m > lastActivityUtc) lastActivityUtc = m;
                }
            }
            if (lastActivityUtc != DateTime.MinValue)
            {
                var age = DateTime.UtcNow - lastActivityUtc;
                if (age.TotalMinutes >= 15) stall = "STALL " + ((int)age.TotalMinutes) + "m";
                else stall = ((int)age.TotalSeconds) + "s ago";
            }
            else stall = "no activity";
        }
        catch { }
        strip.Text = string.Format("CPU {0:0}%   GPU {1}   Llama {2}   Overseer {3}   Activity {4}",
            cpu, gpu, llama, ov, stall);
        if (stall.StartsWith("STALL")) strip.ForeColor = Color.FromArgb(230, 120, 90);
        else if (llama == "down") strip.ForeColor = Color.FromArgb(230, 180, 80);
        else strip.ForeColor = Color.FromArgb(180, 220, 170);
    }

    private float ReadCpu()
    {
        try
        {
            using (var pc = new System.Diagnostics.PerformanceCounter("Processor", "% Processor Time", "_Total"))
            {
                pc.NextValue();
                System.Threading.Thread.Sleep(80);
                return pc.NextValue();
            }
        }
        catch { return -1; }
    }

    private string ReadGpu()
    {
        try
        {
            var psi = new ProcessStartInfo
            {
                FileName = "nvidia-smi",
                Arguments = "--query-gpu=utilization.gpu,temperature.gpu,memory.used,memory.total --format=csv,noheader,nounits",
                UseShellExecute = false,
                RedirectStandardOutput = true,
                CreateNoWindow = true
            };
            using (var p = Process.Start(psi))
            {
                var o = p.StandardOutput.ReadToEnd().Trim();
                p.WaitForExit(1500);
                if (o.Length == 0) return "-";
                var parts = o.Split(',');
                if (parts.Length >= 4)
                    return parts[0].Trim() + "% " + parts[1].Trim() + "C " + parts[2].Trim() + "/" + parts[3].Trim() + "MiB";
                return o.Replace("\n", " ");
            }
        }
        catch { return "n/a"; }
    }

    private static bool Probe(string url)
    {
        try
        {
            var req = (HttpWebRequest)WebRequest.Create(url);
            req.Timeout = 700;
            using (var resp = (HttpWebResponse)req.GetResponse())
                return resp.StatusCode == HttpStatusCode.OK;
        }
        catch { return false; }
    }

    private void UpdateOrch(bool force)
    {
        var sb = new StringBuilder();
        var tok = "";
        var tf = Path.Combine(jobDir, "bridge_token.txt");
        if (File.Exists(tf)) tok = File.ReadAllText(tf).Trim();
        if (tok.Length > 0)
        {
            try
            {
                var req = (HttpWebRequest)WebRequest.Create("http://127.0.0.1:8787/api/status?token=" + Uri.EscapeDataString(tok));
                req.Timeout = 1200;
                using (var resp = (HttpWebResponse)req.GetResponse())
                using (var sr = new StreamReader(resp.GetResponseStream()))
                    sb.AppendLine(PrettyJson(sr.ReadToEnd()));
            }
            catch (Exception ex) { sb.AppendLine("(bridge) " + ex.Message); }
        }
        var sp = Path.Combine(jobDir, "state.json");
        if (File.Exists(sp))
        {
            sb.AppendLine("--- state.json ---");
            sb.AppendLine(PrettyJson(File.ReadAllText(sp)));
        }
        var lp = Path.Combine(jobDir, "overseer.log");
        if (File.Exists(lp))
        {
            var lines = TailLines(lp, 40);
            sb.AppendLine("--- overseer.log (tail) ---");
            sb.AppendLine(lines);
        }
        var text = sb.ToString();
        if (force || text != lastOrch)
        {
            lastOrch = text;
            orch.Text = text;
            orch.SelectionStart = orch.TextLength;
            orch.ScrollToCaret();
        }
    }

    private void UpdateTrace(bool force)
    {
        var path = Path.Combine(jobDir, "llm_trace.jsonl");
        if (!File.Exists(path)) path = Path.Combine(jobDir, "llm_trace.jsonl");
        // also try alternate name used by LiveLlmForm
        if (!File.Exists(path))
        {
            var alt = Path.Combine(jobDir, "llm_trace.jsonl");
            path = alt;
        }
        var candidates = new[] {
            Path.Combine(jobDir, "llm_trace.jsonl"),
            Path.Combine(jobDir, "llm_trace.jsonl")
        };
        // LiveLlmForm used llm_trace.jsonl — check both spellings present in codebase
        string found = null;
        foreach (var c in new[] { "llm_trace.jsonl", "llm_trace.jsonl", "trace.jsonl" })
        {
            var p = Path.Combine(jobDir, c);
            if (File.Exists(p)) { found = p; break; }
        }
        // Fix: actually Live form used "llm_trace.jsonl" from earlier read at line 823 - wait it was llm_trace.jsonl
        found = null;
        foreach (var c in Directory.Exists(jobDir) ? Directory.GetFiles(jobDir, "*.jsonl") : new string[0])
        {
            var n = Path.GetFileName(c).ToLowerInvariant();
            if (n.Contains("llm") || n.Contains("trace")) { found = c; break; }
        }
        if (found == null)
        {
            if (force && promptBox.Text.Length == 0)
            {
                promptBox.Text = "(no llm_trace.jsonl yet)";
                responseBox.Text = "(no response yet)";
            }
            return;
        }
        var lines = TailLines(found, 30).Split(new[] { '\n' }, StringSplitOptions.RemoveEmptyEntries);
        string prompt = "", resp = "";
        for (int i = lines.Length - 1; i >= 0; i--)
        {
            var line = lines[i].Trim();
            if (line.Length == 0) continue;
            if (prompt.Length == 0)
            {
                var p = ExtractJson(line, "prompt");
                if (p.Length == 0) p = ExtractJson(line, "input");
                if (p.Length > 0) prompt = p;
            }
            if (resp.Length == 0)
            {
                var r = ExtractJson(line, "response");
                if (r.Length == 0) r = ExtractJson(line, "content");
                if (r.Length == 0) r = ExtractJson(line, "output");
                if (r.Length > 0) resp = r;
            }
            if (prompt.Length > 0 && resp.Length > 0) break;
        }
        prompt = Unescape(prompt);
        resp = Unescape(resp);
        if (force || prompt != lastPrompt)
        {
            lastPrompt = prompt;
            promptBox.Text = prompt.Length > 0 ? prompt : "(empty)";
        }
        if (force || resp != lastResp)
        {
            lastResp = resp;
            responseBox.Text = resp.Length > 0 ? resp : "(empty)";
        }
        lastActivityUtc = File.GetLastWriteTimeUtc(found);
    }

    private void UpdateDiffs(bool force)
    {
        var sb = new StringBuilder();
        if (Directory.Exists(jobDir))
        {
            foreach (var f in Directory.GetFiles(jobDir, "*.jsonl"))
            {
                var name = Path.GetFileName(f).ToLowerInvariant();
                if (!(name.Contains("tool") || name.Contains("code") || name.Contains("write") || name.Contains("diff") || name.Contains("llm")))
                    continue;
                foreach (var line in TailLines(f, 50).Split(new[] { '\n' }, StringSplitOptions.RemoveEmptyEntries))
                {
                    var tool = ExtractJson(line, "tool");
                    if (tool.Length == 0) tool = ExtractJson(line, "name");
                    if (tool.Length == 0 && line.IndexOf("write_file", StringComparison.OrdinalIgnoreCase) < 0
                        && line.IndexOf("str_replace", StringComparison.OrdinalIgnoreCase) < 0
                        && line.IndexOf("path", StringComparison.OrdinalIgnoreCase) < 0)
                        continue;
                    var path = ExtractJson(line, "path");
                    var oldS = ExtractJson(line, "old_str");
                    var newS = ExtractJson(line, "new_str");
                    if (path.Length == 0 && tool.Length == 0) continue;
                    sb.AppendLine("[" + (tool.Length > 0 ? tool : "edit") + "] " + path);
                    if (oldS.Length > 0 || newS.Length > 0)
                    {
                        sb.AppendLine("- " + TrimOne(Unescape(oldS), 240));
                        sb.AppendLine("+ " + TrimOne(Unescape(newS), 240));
                    }
                    else
                    {
                        var content = ExtractJson(line, "content");
                        if (content.Length > 0) sb.AppendLine(TrimOne(Unescape(content), 300));
                        else sb.AppendLine(TrimOne(line, 300));
                    }
                    sb.AppendLine();
                }
            }
        }
        // codeview events if present
        var cv = Path.Combine(jobDir, "codeview.jsonl");
        if (File.Exists(cv))
        {
            sb.AppendLine("--- codeview ---");
            sb.AppendLine(TailLines(cv, 30));
        }
        var text = sb.ToString();
        if (text.Length == 0) text = "(no write/str_replace events yet)";
        if (force || text != lastDiff)
        {
            lastDiff = text;
            diffBox.Text = text;
        }
    }

    private void AppendUnique(TextBox box, string line)
    {
        box.AppendText(line);
        box.SelectionStart = box.TextLength;
        box.ScrollToCaret();
    }

    private static string TailLines(string path, int n)
    {
        try
        {
            var lines = File.ReadAllLines(path);
            int start = Math.Max(0, lines.Length - n);
            return string.Join("\n", lines, start, lines.Length - start);
        }
        catch { return ""; }
    }

    private static string PrettyJson(string s)
    {
        s = (s ?? "").Trim();
        if (s.Length == 0) return s;
        // light indent for readability without a JSON lib
        var sb = new StringBuilder();
        int depth = 0;
        bool inStr = false;
        for (int i = 0; i < s.Length; i++)
        {
            char c = s[i];
            if (c == '"' && (i == 0 || s[i - 1] != '\\')) inStr = !inStr;
            if (!inStr && (c == '{' || c == '['))
            {
                sb.Append(c);
                sb.Append('\n');
                depth++;
                sb.Append(new string(' ', depth * 2));
            }
            else if (!inStr && (c == '}' || c == ']'))
            {
                sb.Append('\n');
                depth = Math.Max(0, depth - 1);
                sb.Append(new string(' ', depth * 2));
                sb.Append(c);
            }
            else if (!inStr && c == ',')
            {
                sb.Append(c);
                sb.Append('\n');
                sb.Append(new string(' ', depth * 2));
            }
            else if (!inStr && c == ':')
            {
                sb.Append(": ");
            }
            else if (!inStr && (c == ' ' || c == '\n' || c == '\r' || c == '\t')) { }
            else sb.Append(c);
        }
        return sb.ToString();
    }

    private static string ExtractJson(string json, string key)
    {
        var needle = "\"" + key + "\":";
        var i = json.IndexOf(needle, StringComparison.Ordinal);
        if (i < 0) return "";
        i += needle.Length;
        while (i < json.Length && (json[i] == ' ' || json[i] == '\t')) i++;
        if (i >= json.Length) return "";
        if (json[i] == '"')
        {
            i++;
            var sb = new StringBuilder();
            while (i < json.Length)
            {
                char ch = json[i++];
                if (ch == '\\' && i < json.Length) { sb.Append('\\'); sb.Append(json[i++]); }
                else if (ch == '"') break;
                else sb.Append(ch);
            }
            return sb.ToString();
        }
        int start = i;
        while (i < json.Length && json[i] != ',' && json[i] != '}' && json[i] != ']') i++;
        return json.Substring(start, i - start).Trim();
    }

    private static string Unescape(string s)
    {
        if (string.IsNullOrEmpty(s)) return "";
        return s.Replace("\\n", "\n").Replace("\\t", "\t").Replace("\\\"", "\"").Replace("\\\\", "\\");
    }

    private static string TrimOne(string s, int n)
    {
        if (s == null) return "";
        s = s.Replace("\r", "");
        return s.Length <= n ? s : s.Substring(0, n) + "...";
    }
}


internal sealed class MainForm : Form
{
    private const int Port = 8080;
    private const string Ctx = "4096";
    private const int MaxLogChars = 250000;
    private const int GpuHudHeight = 168;
    private const int ContextHudHeight = 58;
    private const int OverseerHeight = 108;

    private readonly string demoDir;
    private readonly string binDir;
    private readonly string exePath;
    private readonly string modelPath;
    private readonly string mmprojPath;
    private readonly string webuiPath;
    private readonly string logDir;
    private readonly string logFile;

    private readonly Panel dot;
    private readonly Label status;
    private readonly Label pidLbl;
    private readonly Label healthLbl;
    private readonly Label bindLbl;
    private readonly Label localLbl;
    private readonly Label lanLbl;
    private readonly Label apiLbl;
    private readonly Label msgLbl;
    private readonly Button btnStart;
    private readonly Button btnStop;
    private readonly Button btnRestart;
    private readonly Button btnOpen;
    private readonly Button btnCopy;
    private readonly Button btnClearLog;
    private readonly TextBox logBox;
    private readonly GpuHud gpuHud;
    private readonly ContextHud contextHud;
    private readonly Label logHeader;
    private readonly Label ovTitle;
    private readonly Label ovRootLbl;
    private readonly Label ovStatus;
    private readonly TextBox txtGoal;
    private readonly TextBox txtRoot;
    private readonly Button btnOvStart;
    private readonly Button btnOvStop;
    private readonly Button btnDialogue;
    private readonly Button btnCode;
    private readonly Button btnChat;
    private readonly Button btnOps;
    private DialogueForm dialogueWin;
    private CodeViewForm codeWin;
    private LiveLlmForm liveWin;
    private OpsLiveForm opsWin;
    private Process overseerProc;
    private Process bridgeProc;
    private readonly string pythonExe;
    private readonly string overseerPy;
    private readonly string grokBotPy;
    private readonly string jobDir;
    private readonly System.Windows.Forms.Timer statusTimer;
    private readonly System.Windows.Forms.Timer logTimer;

    private readonly Color bg = Color.FromArgb(16, 20, 16);
    private readonly Color fg = Color.FromArgb(232, 236, 230);
    private readonly Color muted = Color.FromArgb(150, 160, 148);
    private readonly Color green = Color.FromArgb(80, 180, 90);
    private readonly Color red = Color.FromArgb(200, 80, 70);
    private readonly Color amber = Color.FromArgb(210, 160, 60);
    private readonly Color btnBg = Color.FromArgb(42, 52, 42);
    private readonly Color accent = Color.FromArgb(62, 140, 72);
    private readonly Color logBg = Color.FromArgb(10, 12, 10);

    private bool busy;
    private volatile bool gpuStop;
    private string lastMsg = "Closing this window does not stop the server.";
    private long logPos;
    private bool seededLog;
    private Thread gpuThread;

    public MainForm(string demo)
    {
        demoDir = demo;
        binDir = @"C:\Users\edwar\Llama-cpp";
        exePath = Path.Combine(binDir, "llama-server.exe");
        modelPath = @"C:\Users\edwar\prism-ml\models\qwen3-30b-a3b\Qwen_Qwen3-30B-A3B-Q4_K_M.gguf";
        mmprojPath = Path.Combine(demoDir, "models", "bonsai2-gguf", "27B", "Ternary-Bonsai-2-27B-mmproj-Q8_0.gguf");
        webuiPath = Path.Combine(demoDir, "scripts", "webui-config.json");
        logDir = Path.Combine(demoDir, "logs");
        logFile = Path.Combine(logDir, "bonsai-server.log");
        pythonExe = Path.Combine(demoDir, ".venv", "Scripts", "python.exe");
        overseerPy = Path.Combine(demoDir, "agent", "overseer.py");
        grokBotPy = Path.Combine(demoDir, "agent", "grok_bot.py");
        jobDir = Path.Combine(demoDir, "jobs", "active");

        Text = "Local LLM Server (Qwen3-30B-A3B)";
        FormBorderStyle = FormBorderStyle.Sizable;
        MaximizeBox = true;
        MinimizeBox = true;
        StartPosition = FormStartPosition.CenterScreen;
        ClientSize = new Size(780, 880);
        MinimumSize = new Size(700, 780);
        BackColor = bg;
        ForeColor = fg;
        Font = new Font("Segoe UI", 9.5f);
        ShowInTaskbar = true;
        try { Icon = Icon.ExtractAssociatedIcon(exePath); } catch { }

        Controls.Add(MakeLabel("Qwen3-30B-A3B Q4_K_M", 20, 16, 400, 28, fg, 14f, true));
        Controls.Add(MakeLabel("stock llama.cpp  |  CUDA  |  port " + Port, 20, 44, 500, 20, muted, 9f, false));

        dot = new Panel { Size = new Size(14, 14), Location = new Point(22, 82), BackColor = red };
        Controls.Add(dot);

        status = MakeLabel("Stopped", 44, 76, 500, 22, fg, 12f, true);
        pidLbl = MakeLabel("PID  -", 22, 108, 240, 18, muted, 9f, false);
        healthLbl = MakeLabel("Health  -", 270, 108, 240, 18, muted, 9f, false);
        bindLbl = MakeLabel("Bind  -", 22, 128, 700, 18, muted, 9f, false);
        localLbl = MakeLabel("Local  http://127.0.0.1:" + Port, 22, 150, 700, 18, fg, 9f, false);
        lanLbl = MakeLabel("LAN    -", 22, 170, 700, 18, fg, 9f, false);
        apiLbl = MakeLabel("API    -", 22, 190, 700, 18, fg, 9f, false);
        msgLbl = MakeLabel(lastMsg, 22, 214, 700, 20, muted, 8.5f, false);
        Controls.Add(status);
        Controls.Add(pidLbl);
        Controls.Add(healthLbl);
        Controls.Add(bindLbl);
        Controls.Add(localLbl);
        Controls.Add(lanLbl);
        Controls.Add(apiLbl);
        Controls.Add(msgLbl);

        btnStart = MakeButton("Start model", 22, 242, 130);
        btnStop = MakeButton("Stop model", 160, 242, 130);
        btnRestart = MakeButton("Restart model", 298, 242, 130);
        btnOpen = MakeButton("Open model chat", 436, 242, 140);
        btnCopy = MakeButton("Copy API URL", 584, 242, 154);
        btnStart.Click += delegate { RunSafe(StartServer); };
        btnStop.Click += delegate { RunSafe(StopServer); };
        btnRestart.Click += delegate { RunSafe(RestartServer); };
        btnOpen.Click += delegate { Process.Start("http://127.0.0.1:" + Port + "/"); };
        btnCopy.Click += delegate
        {
            try
            {
                var api = GetApiUrl();
                Clipboard.SetText(api);
                lastMsg = "Copied " + api;
                RefreshStatus();
            }
            catch (Exception ex) { lastMsg = ex.Message; RefreshStatus(); }
        };

        contextHud = new ContextHud
        {
            Location = new Point(20, 286),
            Size = new Size(740, ContextHudHeight),
            Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right
        };
        Controls.Add(contextHud);

        ovTitle = MakeLabel("New work — describe the project or task, then Start new work", 20, 352, 740, 18, muted, 8.5f, false);
        txtGoal = NewTextBox(20, 372, 740, "");
        ovRootLbl = MakeLabel("Workspace", 20, 400, 80, 18, muted, 8.5f, false);
        txtRoot = NewTextBox(104, 398, 400, Path.Combine(demoDir, "jobs", "workspace", "blank"));
        txtRoot.Text = Path.Combine(demoDir, "jobs", "workspace", "blank");
        btnOvStart = MakeButton("Start new work", 514, 396, 110);
        btnOvStop = MakeButton("Stop work", 632, 396, 110);
        btnDialogue = MakeButton("Live LLM", 418, 352, 80);
        btnChat = MakeButton("Chat log", 506, 352, 80);
        btnOps = MakeButton("Ops", 330, 352, 80);
        btnCode = MakeButton("Code view", 594, 352, 80);
        ovStatus = MakeLabel("Overseer  idle", 20, 432, 740, 18, muted, 8.5f, false);
        Controls.Add(ovTitle);
        Controls.Add(ovRootLbl);
        Controls.Add(ovStatus);
        btnOvStart.Click += delegate { StartOverseer(); };
        btnOvStop.Click += delegate { StopOverseer(); };
        btnDialogue.Click += delegate { ShowLive(); };
        btnCode.Click += delegate { ShowCodeView(); };
        btnChat.Click += delegate { ShowDialogue(); };
        btnOps.Click += delegate { ShowOpsLive(); };

        gpuHud = new GpuHud
        {
            Location = new Point(20, 352),
            Size = new Size(740, GpuHudHeight),
            Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right
        };
        Controls.Add(gpuHud);

        logHeader = MakeLabel("Server log", 20, 462, 200, 18, muted, 9f, false);
        Controls.Add(logHeader);
        btnClearLog = MakeButton("Clear", 676, 456, 84);
        btnClearLog.Click += delegate { logBox.Clear(); };

        logBox = new TextBox
        {
            Location = new Point(20, 486),
            Size = new Size(740, 274),
            Multiline = true,
            ReadOnly = true,
            ScrollBars = ScrollBars.Both,
            WordWrap = false,
            BackColor = logBg,
            ForeColor = Color.FromArgb(190, 220, 180),
            BorderStyle = BorderStyle.FixedSingle,
            Font = new Font("Consolas", 9f),
            HideSelection = false,
            Anchor = AnchorStyles.Top | AnchorStyles.Bottom | AnchorStyles.Left | AnchorStyles.Right
        };
        Controls.Add(logBox);

        btnStart.Anchor = AnchorStyles.Top | AnchorStyles.Left;
        btnStop.Anchor = AnchorStyles.Top | AnchorStyles.Left;
        btnRestart.Anchor = AnchorStyles.Top | AnchorStyles.Left;
        btnOpen.Anchor = AnchorStyles.Top | AnchorStyles.Left;
        btnCopy.Anchor = AnchorStyles.Top | AnchorStyles.Left;
        btnClearLog.Anchor = AnchorStyles.Top | AnchorStyles.Right;
        logHeader.Anchor = AnchorStyles.Top | AnchorStyles.Left;
        ovTitle.Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right;
        ovStatus.Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right;
        txtGoal.Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right;
        txtRoot.Anchor = AnchorStyles.Top | AnchorStyles.Left;
        btnOvStart.Anchor = AnchorStyles.Top | AnchorStyles.Right;
        btnOvStop.Anchor = AnchorStyles.Top | AnchorStyles.Right;
        btnDialogue.Anchor = AnchorStyles.Top | AnchorStyles.Right;
        btnCode.Anchor = AnchorStyles.Top | AnchorStyles.Right;
        btnChat.Anchor = AnchorStyles.Top | AnchorStyles.Right;
        btnOps.Anchor = AnchorStyles.Top | AnchorStyles.Right;
        ovRootLbl.Anchor = AnchorStyles.Top | AnchorStyles.Left;

        Resize += delegate { LayoutChrome(); };
        LayoutChrome();

        statusTimer = new System.Windows.Forms.Timer { Interval = 1500 };
        statusTimer.Tick += delegate { RefreshStatus(); };
        logTimer = new System.Windows.Forms.Timer { Interval = 250 };
        logTimer.Tick += delegate { PollLog(); PollContext(); PollOverseer(); };

        Shown += delegate
        {
            TopMost = true;
            Activate();
            BringToFront();
            TopMost = false;
            RefreshStatus();
            PollContext();
            PollLog();
            if (FindServer() != null && !File.Exists(logFile))
            {
                AppendUi("No live log for the current process. Click Restart to capture timings (prompt tok/s, eval tok/s).");
            }
            statusTimer.Start();
            logTimer.Start();
            gpuThread = new Thread(GpuLoop) { IsBackground = true, Name = "gpu-poll" };
            gpuThread.Start();
            // Always launch the phone HTTP session (formerly the Phone button).
            BeginInvoke(new Action(StartPhoneBridge));
        };
        FormClosed += delegate
        {
            gpuStop = true;
            statusTimer.Stop();
            logTimer.Stop();
        };

    }

    private void LayoutChrome()
    {
        int w = Math.Max(100, ClientSize.Width - 40);
        contextHud.Width = w;
        int y = contextHud.Bottom + 8;
        ovTitle.Top = y;
        ovTitle.Width = Math.Max(80, w - 380);
        btnCode.Top = y - 2;
        btnChat.Top = y - 2;
        btnDialogue.Top = y - 2;
        btnOps.Top = y - 2;
        btnCode.Left = Math.Max(20, ClientSize.Width - 20 - btnCode.Width);
        btnChat.Left = btnCode.Left - 8 - btnChat.Width;
        btnDialogue.Left = btnChat.Left - 8 - btnDialogue.Width;
        btnOps.Left = btnDialogue.Left - 8 - btnOps.Width;
        txtGoal.Top = y + 20;
        txtGoal.Width = w;
        ovRootLbl.Top = y + 48;
        txtRoot.Top = y + 46;
        btnOvStart.Top = y + 42;
        btnOvStop.Top = y + 42;
        btnOvStop.Left = Math.Max(20, ClientSize.Width - 20 - btnOvStop.Width);
        btnOvStart.Left = btnOvStop.Left - 8 - btnOvStart.Width;
        txtRoot.Left = 104;
        txtRoot.Width = Math.Max(80, btnOvStart.Left - 8 - txtRoot.Left);
        btnOvStart.BringToFront();
        btnOvStop.BringToFront();
        ovStatus.Top = y + 78;
        ovStatus.Width = w;
        gpuHud.Top = y + OverseerHeight;
        gpuHud.Width = w;
        logHeader.Top = gpuHud.Bottom + 8;
        btnClearLog.Top = gpuHud.Bottom + 2;
        btnClearLog.Left = Math.Max(20, ClientSize.Width - 104);
        logBox.Top = gpuHud.Bottom + 32;
        logBox.Width = w;
        logBox.Height = Math.Max(80, ClientSize.Height - logBox.Top - 20);
    }

    private Label MakeLabel(string text, int x, int y, int w, int h, Color color, float size, bool bold)
    {
        return new Label
        {
            Text = text,
            Location = new Point(x, y),
            Size = new Size(w, h),
            ForeColor = color,
            BackColor = Color.Transparent,
            Font = new Font("Segoe UI", size, bold ? FontStyle.Bold : FontStyle.Regular)
        };
    }

    private TextBox NewTextBox(int x, int y, int w, string text)
    {
        var t = new TextBox
        {
            Location = new Point(x, y),
            Size = new Size(w, 24),
            Text = text,
            BackColor = Color.FromArgb(28, 34, 28),
            ForeColor = fg,
            BorderStyle = BorderStyle.FixedSingle
        };
        Controls.Add(t);
        return t;
    }

    private Button MakeButton(string text, int x, int y, int w)
    {
        var b = new Button
        {
            Text = text,
            Location = new Point(x, y),
            Size = new Size(w, 34),
            FlatStyle = FlatStyle.Flat,
            BackColor = btnBg,
            ForeColor = fg,
            Cursor = Cursors.Hand
        };
        b.FlatAppearance.BorderSize = 0;
        Controls.Add(b);
        return b;
    }

    private void GpuLoop()
    {
        string smi = FindNvidiaSmi();
        if (smi == null)
        {
            gpuHud.SetError("nvidia-smi not found");
            return;
        }
        try
        {
            var name = RunSmi(smi, "--query-gpu=name --format=csv,noheader");
            if (!string.IsNullOrEmpty(name)) gpuHud.SetName(name.Trim());
        }
        catch { }

        while (!gpuStop)
        {
            try
            {
                var line = RunSmi(smi, "--query-gpu=utilization.gpu,temperature.gpu --format=csv,noheader,nounits");
                if (string.IsNullOrEmpty(line))
                {
                    gpuHud.SetError("nvidia-smi returned no data");
                }
                else
                {
                    var parts = line.Split(',');
                    float util, temp;
                    if (parts.Length >= 2 &&
                        float.TryParse(parts[0].Trim(), out util) &&
                        float.TryParse(parts[1].Trim(), out temp))
                    {
                        gpuHud.AddSample(util, temp);
                    }
                    else gpuHud.SetError("Could not parse: " + line.Trim());
                }
            }
            catch (Exception ex)
            {
                gpuHud.SetError(ex.Message);
            }
            for (int i = 0; i < 10 && !gpuStop; i++) Thread.Sleep(50);
        }
    }

    private static string FindNvidiaSmi()
    {
        var p1 = Path.Combine(Environment.SystemDirectory, "nvidia-smi.exe");
        if (File.Exists(p1)) return p1;
        var p2 = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles),
            "NVIDIA Corporation", "NVSMI", "nvidia-smi.exe");
        if (File.Exists(p2)) return p2;
        return null;
    }

    private static string RunSmi(string exe, string args)
    {
        var psi = new ProcessStartInfo
        {
            FileName = exe,
            Arguments = args,
            CreateNoWindow = true,
            UseShellExecute = false,
            RedirectStandardOutput = true,
            RedirectStandardError = true
        };
        using (var p = Process.Start(psi))
        {
            if (p == null) return null;
            var output = p.StandardOutput.ReadToEnd();
            p.WaitForExit(2000);
            if (string.IsNullOrWhiteSpace(output)) return null;
            var line = output.Replace("\r", "").Trim();
            var nl = line.IndexOf('\n');
            return nl >= 0 ? line.Substring(0, nl).Trim() : line;
        }
    }

    private Process FindServer()
    {
        foreach (var p in Process.GetProcessesByName("llama-server"))
        {
            try
            {
                var path = p.MainModule != null ? p.MainModule.FileName : null;
                if (path != null && (path.StartsWith(demoDir, StringComparison.OrdinalIgnoreCase) || path.IndexOf("Llama-cpp", StringComparison.OrdinalIgnoreCase) >= 0))
                    return p;
            }
            catch { }
        }
        return null;
    }

    private static bool PortListening()
    {
        try
        {
            foreach (var ep in IPGlobalProperties.GetIPGlobalProperties().GetActiveTcpListeners())
            {
                if (ep.Port == Port) return true;
            }
        }
        catch { }
        return false;
    }

    private static string Health()
    {
        try
        {
            var req = (HttpWebRequest)WebRequest.Create("http://127.0.0.1:" + Port + "/health");
            req.Timeout = 800;
            req.ReadWriteTimeout = 800;
            req.Method = "GET";
            using (var resp = (HttpWebResponse)req.GetResponse())
            using (var s = resp.GetResponseStream())
            using (var r = new StreamReader(s, Encoding.UTF8))
            {
                var body = r.ReadToEnd();
                if (body.IndexOf("\"ok\"", StringComparison.OrdinalIgnoreCase) >= 0) return "ok";
                return "unexpected";
            }
        }
        catch { return "down"; }
    }

    private static string LanIPv4()
    {
        try
        {
            foreach (var nic in NetworkInterface.GetAllNetworkInterfaces())
            {
                if (nic.OperationalStatus != OperationalStatus.Up) continue;
                var name = nic.Name + " " + nic.Description;
                if (name.IndexOf("VPN", StringComparison.OrdinalIgnoreCase) >= 0) continue;
                if (name.IndexOf("Nord", StringComparison.OrdinalIgnoreCase) >= 0) continue;
                if (name.IndexOf("Virtual", StringComparison.OrdinalIgnoreCase) >= 0) continue;
                if (nic.NetworkInterfaceType == NetworkInterfaceType.Loopback) continue;
                foreach (var addr in nic.GetIPProperties().UnicastAddresses)
                {
                    if (addr.Address.AddressFamily != AddressFamily.InterNetwork) continue;
                    var ip = addr.Address.ToString();
                    if (ip.StartsWith("192.168.")) return ip;
                }
            }
        }
        catch { }
        return null;
    }

    private string GetApiUrl()
    {
        var lan = LanIPv4();
        return lan != null ? ("http://" + lan + ":" + Port + "/v1") : ("http://127.0.0.1:" + Port + "/v1");
    }

    private void RefreshStatus()
    {
        try
        {
            var proc = FindServer();
            var running = proc != null && !proc.HasExited;
            var health = running ? Health() : "down";
            var lan = LanIPv4();
            var listening = PortListening();

            if (running && health == "ok")
            {
                status.Text = "Running";
                status.ForeColor = green;
                dot.BackColor = green;
            }
            else if (running)
            {
                status.Text = "Starting / not healthy";
                status.ForeColor = amber;
                dot.BackColor = amber;
            }
            else
            {
                status.Text = "Stopped";
                status.ForeColor = red;
                dot.BackColor = red;
            }

            pidLbl.Text = running ? ("PID  " + proc.Id) : "PID  -";
            healthLbl.Text = "Health  " + health;
            bindLbl.Text = listening ? ("Bind  0.0.0.0:" + Port) : "Bind  (not listening)";
            localLbl.Text = "Local  http://127.0.0.1:" + Port;
            lanLbl.Text = lan != null ? ("LAN    http://" + lan + ":" + Port) : "LAN    (no LAN IP)";
            apiLbl.Text = "API    " + GetApiUrl();
            msgLbl.Text = lastMsg;

            btnStart.Enabled = !running && !busy;
            btnStop.Enabled = running && !busy;
            btnRestart.Enabled = !busy;
            btnOpen.Enabled = health == "ok";
            btnStart.BackColor = btnStart.Enabled ? accent : btnBg;
        }
        catch (Exception ex)
        {
            lastMsg = "Status error: " + ex.Message;
            msgLbl.Text = lastMsg;
        }
    }

    private static int JsonInt(string json, string key, int fallback)
    {
        var needle = "\"" + key + "\":";
        var i = json.IndexOf(needle, StringComparison.Ordinal);
        if (i < 0) return fallback;
        i += needle.Length;
        while (i < json.Length && (json[i] == ' ' || json[i] == '\t')) i++;
        int sign = 1;
        if (i < json.Length && json[i] == '-') { sign = -1; i++; }
        if (i >= json.Length || json[i] < '0' || json[i] > '9') return fallback;
        int v = 0;
        while (i < json.Length && json[i] >= '0' && json[i] <= '9')
        {
            v = v * 10 + (json[i] - '0');
            i++;
        }
        return sign * v;
    }

    private static bool JsonBool(string json, string key, bool fallback)
    {
        var needle = "\"" + key + "\":";
        var i = json.IndexOf(needle, StringComparison.Ordinal);
        if (i < 0) return fallback;
        i += needle.Length;
        while (i < json.Length && (json[i] == ' ' || json[i] == '\t')) i++;
        if (json.IndexOf("true", i, StringComparison.Ordinal) == i) return true;
        if (json.IndexOf("false", i, StringComparison.Ordinal) == i) return false;
        return fallback;
    }

    private static string HttpGet(string url, int timeoutMs)
    {
        var req = (HttpWebRequest)WebRequest.Create(url);
        req.Timeout = timeoutMs;
        req.ReadWriteTimeout = timeoutMs;
        req.Method = "GET";
        using (var resp = (HttpWebResponse)req.GetResponse())
        using (var s = resp.GetResponseStream())
        using (var r = new StreamReader(s, Encoding.UTF8))
            return r.ReadToEnd();
    }

    private void PollContext()
    {
        try
        {
            if (FindServer() == null)
            {
                contextHud.SetIdle();
                return;
            }
            var json = HttpGet("http://127.0.0.1:" + Port + "/slots", 600);
            int nCtx = JsonInt(json, "n_ctx", 0);
            int nPrompt = JsonInt(json, "n_prompt_tokens", 0);
            int nGen = JsonInt(json, "n_decoded", 0);
            bool busySlot = JsonBool(json, "is_processing", false);
            int used = nPrompt + (busySlot ? Math.Max(0, nGen) : 0);
            if (!busySlot && nPrompt > 0) used = nPrompt;
            if (used > nCtx && nCtx > 0) used = nCtx;
            contextHud.SetValues(nCtx, used, nPrompt, busySlot ? nGen : Math.Max(0, used - nPrompt), busySlot);
        }
        catch
        {
            contextHud.SetIdle();
        }
    }

    private void PollLog()
    {
        try
        {
            if (!File.Exists(logFile)) return;
            using (var fs = new FileStream(logFile, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
            {
                if (fs.Length < logPos) logPos = 0;
                if (!seededLog && fs.Length > 65536)
                {
                    logPos = fs.Length - 65536;
                    int b;
                    while (logPos < fs.Length && (b = fs.ReadByte()) != -1)
                    {
                        logPos++;
                        if (b == 10) break;
                    }
                    seededLog = true;
                }
                if (fs.Length == logPos) return;
                fs.Seek(logPos, SeekOrigin.Begin);
                var buf = new byte[fs.Length - logPos];
                var n = fs.Read(buf, 0, buf.Length);
                logPos = fs.Position;
                seededLog = true;
                if (n > 0) AppendUi(Encoding.UTF8.GetString(buf, 0, n));
            }
        }
        catch { }
    }

    private void AppendUi(string chunk)
    {
        if (string.IsNullOrEmpty(chunk)) return;
        chunk = chunk.Replace("\r\n", "\n").Replace("\r", "\n").Replace("\n", "\r\n");
        if (logBox.TextLength > MaxLogChars)
        {
            var keep = logBox.Text.Substring(logBox.TextLength - (MaxLogChars / 2));
            var cut = keep.IndexOf("\r\n");
            logBox.Text = cut >= 0 ? keep.Substring(cut + 2) : keep;
        }
        logBox.AppendText(chunk);
        logBox.SelectionStart = logBox.TextLength;
        logBox.ScrollToCaret();
    }

    private void RunSafe(Action work)
    {
        if (busy) return;
        busy = true;
        btnStart.Enabled = btnStop.Enabled = btnRestart.Enabled = false;
        try { work(); }
        catch (Exception ex)
        {
            lastMsg = ex.Message;
            MessageBox.Show(ex.Message, "Local LLM Server", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
        finally
        {
            busy = false;
            RefreshStatus();
        }
    }

    private void StartServer()
    {
        if (FindServer() != null) { lastMsg = "Already running."; return; }
        if (!File.Exists(exePath)) throw new Exception("llama-server.exe not found:\n" + exePath);
        if (!File.Exists(modelPath)) throw new Exception("Model not found:\n" + modelPath);
        if (PortListening()) throw new Exception("Port " + Port + " is already in use.");

        Directory.CreateDirectory(logDir);
        try { if (File.Exists(logFile)) File.Delete(logFile); } catch { }
        logPos = 0;
        seededLog = true;
        logBox.Clear();
        AppendUi("Starting llama-server...\r\n");

        var psi = new ProcessStartInfo
        {
            FileName = exePath,
            WorkingDirectory = binDir,
            UseShellExecute = false,
            CreateNoWindow = true,
            Arguments = string.Join(" ", new[]
            {
                "-m", Quote(modelPath),
                "--host", "0.0.0.0", "--port", Port.ToString(),
                "-ngl", "99", "-fa", "on", "-c", Ctx,
                "--temp", "0.2", "--top-p", "0.9", "--top-k", "20", "--jinja",
                
                "--webui-config-file", Quote(webuiPath),
                "--log-file", Quote(logFile),
                "-np", "1"
            })
        };
        var p = Process.Start(psi);
        if (p == null) throw new Exception("Failed to start llama-server.");
        lastMsg = "Starting (PID " + p.Id + ")...";
    }

    private string CurrentRoot()
    {
        var root = (txtRoot.Text ?? "").Trim();
        if (root.Length == 0) root = Path.Combine(demoDir, "jobs", "workspace", "blank");
        return root;
    }

    private string CurrentJobDir()
    {
        return Path.Combine(CurrentRoot(), ".overseer");
    }


    private void ShowOpsLive()
    {
        var dir = CurrentJobDir();
        if (opsWin != null && !opsWin.IsDisposed && opsWin.Tag as string != dir)
        {
            opsWin.Close();
            opsWin = null;
        }
        if (opsWin == null || opsWin.IsDisposed)
        {
            opsWin = new OpsLiveForm(dir, demoDir);
            opsWin.Tag = dir;
            opsWin.StartPosition = FormStartPosition.Manual;
            opsWin.Location = new Point(Math.Max(0, Left - 40), Math.Max(0, Top - 20));
        }
        opsWin.Show(this);
        opsWin.BringToFront();
        if (opsWin.WindowState == FormWindowState.Minimized)
            opsWin.WindowState = FormWindowState.Normal;
    }

    private void ShowLive()
    {
        var dir = CurrentJobDir();
        if (liveWin != null && !liveWin.IsDisposed && liveWin.Tag as string != dir)
        {
            liveWin.Close();
            liveWin = null;
        }
        if (liveWin == null || liveWin.IsDisposed)
        {
            liveWin = new LiveLlmForm(dir);
            liveWin.Tag = dir;
            liveWin.StartPosition = FormStartPosition.Manual;
            liveWin.Location = new Point(Math.Max(0, Left - liveWin.Width - 8), Top);
        }
        liveWin.Show(this);
        liveWin.BringToFront();
        if (liveWin.WindowState == FormWindowState.Minimized)
            liveWin.WindowState = FormWindowState.Normal;
    }

    private void ShowDialogue()
    {
        var dir = CurrentJobDir();
        if (dialogueWin != null && !dialogueWin.IsDisposed && dialogueWin.Tag as string != dir)
        {
            dialogueWin.Close();
            dialogueWin = null;
        }
        if (dialogueWin == null || dialogueWin.IsDisposed)
        {
            dialogueWin = new DialogueForm(dir);
            dialogueWin.Tag = dir;
            dialogueWin.StartPosition = FormStartPosition.Manual;
            dialogueWin.Location = new Point(Right + 8, Top);
        }
        dialogueWin.Show(this);
        dialogueWin.BringToFront();
        if (dialogueWin.WindowState == FormWindowState.Minimized)
            dialogueWin.WindowState = FormWindowState.Normal;
    }

    private void ShowCodeView()
    {
        var dir = CurrentJobDir();
        var root = CurrentRoot();
        var tag = dir + "|" + root;
        if (codeWin != null && !codeWin.IsDisposed && codeWin.Tag as string != tag)
        {
            codeWin.Close();
            codeWin = null;
        }
        if (codeWin == null || codeWin.IsDisposed)
        {
            codeWin = new CodeViewForm(dir, root);
            codeWin.Tag = tag;
            codeWin.StartPosition = FormStartPosition.Manual;
            int x = Right + 8;
            int y = Top;
            if (dialogueWin != null && !dialogueWin.IsDisposed)
                y = dialogueWin.Bottom + 8;
            codeWin.Location = new Point(x, y);
        }
        codeWin.Show(this);
        codeWin.BringToFront();
        if (codeWin.WindowState == FormWindowState.Minimized)
            codeWin.WindowState = FormWindowState.Normal;
    }

    private void StartPhoneBridge()
    {
        try
        {
            string tok = "";
            var tf = Path.Combine(CurrentJobDir(), "bridge_token.txt");
            bool httpUp = false;
            try
            {
                if (File.Exists(tf)) tok = File.ReadAllText(tf).Trim();
                var chk = (HttpWebRequest)WebRequest.Create("http://127.0.0.1:8787/api/status?token=" + tok);
                chk.Timeout = 400;
                using (var resp = (HttpWebResponse)chk.GetResponse())
                    httpUp = resp.StatusCode == HttpStatusCode.OK;
            }
            catch { httpUp = false; }

            bool alive = false;
            try { alive = bridgeProc != null && !bridgeProc.HasExited; } catch { }
            if (!alive && !httpUp)
            {
                var psi = new ProcessStartInfo
                {
                    FileName = pythonExe,
                    Arguments = "-u " + Quote(grokBotPy),
                    WorkingDirectory = Path.Combine(demoDir, "agent"),
                    UseShellExecute = false,
                    CreateNoWindow = true
                };
                psi.EnvironmentVariables["PYTHONPATH"] = Path.Combine(demoDir, "agent");
                bridgeProc = Process.Start(psi);
            }
            for (int i = 0; i < 30 && !httpUp; i++)
            {
                Thread.Sleep(200);
                if (File.Exists(tf)) tok = File.ReadAllText(tf).Trim();
                if (tok.Length == 0) continue;
                try
                {
                    var chk = (HttpWebRequest)WebRequest.Create("http://127.0.0.1:8787/api/status?token=" + tok);
                    chk.Timeout = 400;
                    using (var resp = (HttpWebResponse)chk.GetResponse())
                        httpUp = resp.StatusCode == HttpStatusCode.OK;
                }
                catch { }
            }
            if (!httpUp)
            {
                var blog = Path.Combine(CurrentJobDir(), "bridge.log");
                var extra = File.Exists(blog) ? "\n\n" + File.ReadAllText(blog) : "";
                throw new Exception("Port 8787 did not come up." + extra);
            }
            var lan = LanIPv4() ?? "127.0.0.1";
            var url = "http://" + lan + ":8787/?token=" + tok;
            lastMsg = "Phone bridge  " + url;
            Clipboard.SetText(url);
            Process.Start(new ProcessStartInfo { FileName = url, UseShellExecute = true });
            ShowLive();
            RefreshStatus();
        }
        catch (Exception ex)
        {
            MessageBox.Show(ex.Message, "Phone bridge", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }

    private bool OverseerAlive()
    {
        try { return overseerProc != null && !overseerProc.HasExited; }
        catch { return false; }
    }

    private void StartOverseer()
    {
      try {
        if (OverseerAlive())
        {
            lastMsg = "Overseer already running.";
            RefreshStatus();
            return;
        }
        var goal = (txtGoal.Text ?? "").Trim();
        var root = CurrentRoot();
        var ovDir = CurrentJobDir();
        if (goal.Length == 0 && !File.Exists(Path.Combine(ovDir, "state.json"))
            && !File.Exists(Path.Combine(demoDir, "jobs", "active", "state.json")))
        {
            MessageBox.Show("Enter a goal, or point Workspace at a folder that already has .overseer\\state.json to resume.", "Local LLM Server", MessageBoxButtons.OK, MessageBoxIcon.Information);
            return;
        }
        if (!File.Exists(pythonExe))
        {
            MessageBox.Show("Python venv not found:\n" + pythonExe, "Local LLM Server", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return;
        }
        if (!File.Exists(overseerPy))
        {
            MessageBox.Show("overseer.py not found:\n" + overseerPy, "Local LLM Server", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return;
        }
        Directory.CreateDirectory(root);
        Directory.CreateDirectory(ovDir);
        var stopFile = Path.Combine(ovDir, "STOP");
        if (File.Exists(stopFile)) File.Delete(stopFile);

        var args = "-u " + Quote(overseerPy)
            + " --root " + Quote(root)
            + " --api " + Quote("http://127.0.0.1:" + Port + "/v1");
        if (goal.Length > 0) args += " --goal " + Quote(goal);

        var psi = new ProcessStartInfo
        {
            FileName = pythonExe,
            Arguments = args,
            WorkingDirectory = demoDir,
            UseShellExecute = false,
            CreateNoWindow = true
        };
        overseerProc = Process.Start(psi);
        if (overseerProc == null) throw new Exception("Failed to start overseer.");
        lastMsg = "Overseer started (PID " + overseerProc.Id + "). Closing this window does not stop it.";
        ovStatus.Text = "Overseer  starting PID " + overseerProc.Id;
        ovStatus.ForeColor = amber;
        ShowDialogue();
        ShowCodeView();
        RefreshStatus();
      } catch (Exception ex) {
        MessageBox.Show(ex.Message, "Local LLM Server", MessageBoxButtons.OK, MessageBoxIcon.Error);
      }
    }

    private void StopOverseer()
    {
        try
        {
            Directory.CreateDirectory(CurrentJobDir());
            File.WriteAllText(Path.Combine(CurrentJobDir(), "STOP"), utcStamp());
        }
        catch { }
        if (OverseerAlive())
        {
            try { overseerProc.Kill(); } catch { }
        }
        overseerProc = null;
        ovStatus.Text = "Overseer  stop requested";
        ovStatus.ForeColor = muted;
        lastMsg = "Overseer stop requested.";
        RefreshStatus();
    }

    private static string utcStamp()
    {
        return DateTime.UtcNow.ToString("o");
    }

    private void PollOverseer()
    {
        try
        {
            bool alive = OverseerAlive();
            if (!alive)
            {
                var pidFile = Path.Combine(CurrentJobDir(), "overseer.pid");
                int pid;
                if (File.Exists(pidFile) && int.TryParse(File.ReadAllText(pidFile).Trim(), out pid))
                {
                    try
                    {
                        var p = Process.GetProcessById(pid);
                        if (p != null && !p.HasExited)
                        {
                            overseerProc = p;
                            alive = true;
                        }
                    }
                    catch { }
                }
            }

            var statePath = Path.Combine(CurrentJobDir(), "state.json");
            if (File.Exists(statePath))
            {
                var json = File.ReadAllText(statePath);
                var st = JsonString(json, "status", alive ? "running" : "idle");
                var cur = JsonString(json, "current", "");
                var art = JsonString(json, "artifact", "");
                string line = "Overseer  " + st;
                if (!string.IsNullOrEmpty(cur)) line += "   task " + cur;
                if (st == "done" && !string.IsNullOrEmpty(art)) line += "   exe " + art;
                if (st == "blocked")
                {
                    var reason = JsonString(json, "block_reason", "");
                    if (!string.IsNullOrEmpty(reason)) line += "   " + reason;
                    ovStatus.ForeColor = red;
                }
                else if (st == "done") ovStatus.ForeColor = green;
                else if (st == "running" || st == "waiting") ovStatus.ForeColor = amber;
                else ovStatus.ForeColor = muted;
                ovStatus.Text = line;
            }
            else if (!alive)
            {
                ovStatus.Text = "Overseer  idle";
                ovStatus.ForeColor = muted;
            }
        }
        catch { }
    }

    private static string JsonString(string json, string key, string fallback)
    {
        var needle = "\"" + key + "\":";
        var i = json.IndexOf(needle, StringComparison.Ordinal);
        if (i < 0) return fallback;
        i += needle.Length;
        while (i < json.Length && (json[i] == ' ' || json[i] == '\t')) i++;
        if (i < json.Length && json[i] == '"')
        {
            i++;
            var sb = new StringBuilder();
            while (i < json.Length)
            {
                char c = json[i++];
                if (c == '\\' && i < json.Length)
                {
                    char n = json[i++];
                    sb.Append(n == 'n' ? '\n' : n);
                }
                else if (c == '"') break;
                else sb.Append(c);
            }
            return sb.ToString();
        }
        if (json.IndexOf("null", i, StringComparison.Ordinal) == i) return fallback;
        return fallback;
    }

    private void StopServer()
    {
        var p = FindServer();
        if (p == null) { lastMsg = "Already stopped."; return; }
        try { p.Kill(); } catch { }
        var until = DateTime.UtcNow.AddSeconds(8);
        while (DateTime.UtcNow < until)
        {
            try { if (p.HasExited) break; } catch { break; }
            Thread.Sleep(200);
        }
        lastMsg = "Stopped.";
    }

    private void RestartServer()
    {
        if (FindServer() != null) StopServer();
        StartServer();
    }

    private static string Quote(string path)
    {
        return "\"" + path + "\"";
    }
}

internal static class Program
{
    [STAThread]
    private static void Main()
    {
        bool created;
        using (var mutex = new Mutex(true, @"Local\BonsaiServerGui", out created))
        {
            if (!created)
            {
                foreach (var p in Process.GetProcessesByName("BonsaiServerGui"))
                {
                    if (p.Id == Process.GetCurrentProcess().Id) continue;
                    try
                    {
                        if (p.MainWindowHandle != IntPtr.Zero)
                        {
                            if (Native.IsIconic(p.MainWindowHandle))
                                Native.ShowWindow(p.MainWindowHandle, Native.SW_RESTORE);
                            Native.SetForegroundWindow(p.MainWindowHandle);
                        }
                    }
                    catch { }
                }
                return;
            }

            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);

            string demoDir;
            try
            {
                var exe = Process.GetCurrentProcess().MainModule.FileName;
                var scripts = Path.GetDirectoryName(exe);
                demoDir = Path.GetFullPath(Path.Combine(scripts, ".."));
            }
            catch
            {
                demoDir = Path.GetFullPath(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, ".."));
            }

            try
            {
                Application.Run(new MainForm(demoDir));
            }
            catch (Exception ex)
            {
                try
                {
                    var log = Path.Combine(demoDir, "logs", "bonsai-gui.log");
                    Directory.CreateDirectory(Path.GetDirectoryName(log));
                    File.AppendAllText(log, DateTime.Now + " " + ex + Environment.NewLine);
                }
                catch { }
                MessageBox.Show(ex.ToString(), "Bonsai Server failed to start", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
        }
    }
}
