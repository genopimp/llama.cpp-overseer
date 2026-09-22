from pathlib import Path
import re, shutil, time, subprocess, os

cs = Path(r"C:\Users\edwar\prism-ml\scripts\BonsaiServerGui.cs")
src = cs.read_text(encoding="utf-8")

OPS = r'''
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

'''

# Insert OpsLiveForm before MainForm
anchor = "internal sealed class MainForm : Form"
if "class OpsLiveForm" in src:
    print("OpsLiveForm already present")
else:
    if anchor not in src:
        raise SystemExit("MainForm anchor missing")
    src = src.replace(anchor, OPS + "\n" + anchor, 1)
    print("inserted OpsLiveForm")

# Add field
if "OpsLiveForm opsWin" not in src and "opsWin" not in src:
    src = src.replace(
        "private LiveLlmForm liveWin;",
        "private LiveLlmForm liveWin;\n    private OpsLiveForm opsWin;",
        1,
    )
    print("added opsWin field")

# Add button field
if "btnOps" not in src:
    src = src.replace(
        "private readonly Button btnChat;",
        "private readonly Button btnChat;\n    private readonly Button btnOps;",
        1,
    )
    print("added btnOps field")

# Create button near Chat
if 'MakeButton("Ops Live"' not in src and 'MakeButton("Ops"' not in src:
    src = src.replace(
        'btnChat = MakeButton("Chat", 506, 352, 80);',
        'btnChat = MakeButton("Chat", 506, 352, 80);\n        btnOps = MakeButton("Ops Live", 330, 352, 80);',
        1,
    )
    print("created btnOps")

# Click handler
if "ShowOpsLive" not in src:
    src = src.replace(
        "btnChat.Click += delegate { ShowDialogue(); };",
        "btnChat.Click += delegate { ShowDialogue(); };\n        btnOps.Click += delegate { ShowOpsLive(); };",
        1,
    )
    print("wired click")

# Anchor
if "btnOps.Anchor" not in src:
    src = src.replace(
        "btnChat.Anchor = AnchorStyles.Top | AnchorStyles.Right;",
        "btnChat.Anchor = AnchorStyles.Top | AnchorStyles.Right;\n        btnOps.Anchor = AnchorStyles.Top | AnchorStyles.Right;",
        1,
    )
    print("anchor")

# LayoutChrome
if "btnOps.Top" not in src:
    old = """        btnCode.Top = y - 2;
        btnChat.Top = y - 2;
        btnDialogue.Top = y - 2;
        btnCode.Left = Math.Max(20, ClientSize.Width - 20 - btnCode.Width);
        btnChat.Left = btnCode.Left - 8 - btnChat.Width;
        btnDialogue.Left = btnChat.Left - 8 - btnDialogue.Width;"""
    new = """        btnCode.Top = y - 2;
        btnChat.Top = y - 2;
        btnDialogue.Top = y - 2;
        btnOps.Top = y - 2;
        btnCode.Left = Math.Max(20, ClientSize.Width - 20 - btnCode.Width);
        btnChat.Left = btnCode.Left - 8 - btnChat.Width;
        btnDialogue.Left = btnChat.Left - 8 - btnDialogue.Width;
        btnOps.Left = btnDialogue.Left - 8 - btnOps.Width;"""
    if old not in src:
        raise SystemExit("LayoutChrome block not found")
    src = src.replace(old, new, 1)
    print("layout")

# ShowOpsLive method after ShowLive
if "void ShowOpsLive()" not in src:
    show = '''
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

'''
    marker = "    private void ShowLive()\n"
    if marker not in src:
        raise SystemExit("ShowLive marker missing")
    src = src.replace(marker, show + marker, 1)
    print("added ShowOpsLive")

# Controls.Add for btnOps - find where btnChat is added
if "Controls.Add(btnOps)" not in src:
    # buttons may be added via a helper - search
    if "Controls.Add(btnChat)" in src:
        src = src.replace("Controls.Add(btnChat);", "Controls.Add(btnChat);\n        Controls.Add(btnOps);", 1)
        print("Controls.Add btnOps")
    else:
        # MakeButton likely auto-adds - check MakeButton
        print("NOTE: no Controls.Add(btnChat) — MakeButton may add itself")

cs.write_text(src, encoding="utf-8", newline="\n")
print("wrote", cs, "lines", src.count("\n")+1)

# Check MakeButton
m = re.search(r"Button MakeButton\(.*?\{.*?return", src, re.S)
if m:
    print("MakeButton snippet:", m.group(0)[:300])
