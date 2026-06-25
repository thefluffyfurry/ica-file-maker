import os
import time
import random
import queue
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import requests


PROVIDERS = {
    "LM Studio": {
        "api_url": "http://127.0.0.1:1234/v1/chat/completions",
        "models_url": "http://127.0.0.1:1234/v1/models",
        "requires_key": False,
        "key_label": "LM Studio token (optional):",
        "default_model": "",
        "default_workers": "1",
        "default_rpm": "0",
    },
    "OpenRouter": {
        "api_url": "https://openrouter.ai/api/v1/chat/completions",
        "models_url": "https://openrouter.ai/api/v1/models",
        "requires_key": True,
        "key_label": "OpenRouter API key:",
        "default_model": "openrouter/free",
        "default_workers": "5",
        "default_rpm": "18",
    },
}

LOCATIONS = [
    "Paris, France", "Sapienza, Italy", "Marrakesh, Morocco",
    "Bangkok, Thailand", "Colorado, USA", "Hokkaido, Japan",
    "Miami, USA", "Mumbai, India", "Whittleton Creek, USA",
    "Isle of Sgàil, UK", "Dubai, UAE", "Chongqing, China",
    "Mendoza, Argentina", "Berlin, Germany", "Dartmoor, UK",
]

ALIASES = [
    "The Architect", "The Puppeteer", "The Catalyst", "The Broker",
    "The Pharmacist", "The Conductor", "The Siren", "The Banker",
    "The Arbitrator", "The Ghost",
]

FIRST_NAMES = [
    "Viktor", "Silvio", "Claus", "Jordan", "Sean", "Erich", "Alma",
    "Robert", "Dawood", "Janus", "Carl", "Tamara", "Arthur", "Marcus",
    "Alexa",
]

LAST_NAMES = [
    "Novikov", "Caruso", "Strandberg", "Cross", "Rose", "Soders",
    "Reynard", "Knox", "Rangan", "Fuchs", "Ingram", "Vidal",
    "Edwards", "Stuyvesant", "Carlisle",
]


class GlobalRateLimiter:
    """Spaces request starts across every worker. RPM 0 means unlimited."""

    def __init__(self, requests_per_minute: int):
        self.interval = 0.0 if requests_per_minute <= 0 else 60.0 / requests_per_minute
        self.next_allowed = 0.0
        self.lock = threading.Lock()

    def wait(self, stop_event: threading.Event) -> bool:
        if self.interval <= 0:
            return not stop_event.is_set()

        while not stop_event.is_set():
            with self.lock:
                now = time.monotonic()
                wait_for = max(0.0, self.next_allowed - now)
                if wait_for <= 0:
                    self.next_allowed = max(now, self.next_allowed) + self.interval
                    return True

            if stop_event.wait(min(wait_for, 0.25)):
                return False

        return False


class ICAGeneratorGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("ICA Multi-Maker Dossier Generator")
        self.root.geometry("1220x800")
        self.root.minsize(1000, 680)

        self.events = queue.Queue()
        self.stop_event = threading.Event()
        self.running = False
        self.counter_lock = threading.Lock()

        self.created = 0
        self.skipped = 0
        self.failed = 0
        self.finished_items = 0
        self.total_items = 0
        self.run_started_at = None
        self.folder_items = {}

        self.build_gui()
        self.provider_changed(first_time=True)
        self.root.after(100, self.process_events)
        self.root.after(1000, self.update_time_display)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def build_gui(self):
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)

        settings = ttk.LabelFrame(outer, text="Generator settings", padding=10)
        settings.pack(fill="x")

        self.provider_var = tk.StringVar(value="LM Studio")
        self.api_key_var = tk.StringVar()
        self.model_var = tk.StringVar()
        self.output_var = tk.StringVar(
            value=r"C:\Users\holly\OneDrive\Desktop\ica\ica files"
        )
        self.folder_count_var = tk.StringVar(value="10")
        self.files_per_folder_var = tk.StringVar(value="100")
        self.worker_count_var = tk.StringVar(value="1")
        self.rpm_var = tk.StringVar(value="0")
        self.retries_var = tk.StringVar(value="4")
        self.show_key_var = tk.BooleanVar(value=False)

        ttk.Label(settings, text="Provider:").grid(
            row=0, column=0, sticky="w", padx=(0, 6), pady=4
        )
        self.provider_box = ttk.Combobox(
            settings,
            textvariable=self.provider_var,
            values=list(PROVIDERS.keys()),
            state="readonly",
            width=22,
        )
        self.provider_box.grid(row=0, column=1, sticky="w", pady=4)
        self.provider_box.bind("<<ComboboxSelected>>", lambda _e: self.provider_changed())

        self.key_label = ttk.Label(settings, text="LM Studio token (optional):")
        self.key_label.grid(row=0, column=2, sticky="e", padx=(12, 6), pady=4)

        self.key_entry = ttk.Entry(
            settings, textvariable=self.api_key_var, show="*", width=42
        )
        self.key_entry.grid(row=0, column=3, sticky="ew", pady=4)

        ttk.Checkbutton(
            settings,
            text="Show",
            variable=self.show_key_var,
            command=self.toggle_key,
        ).grid(row=0, column=4, sticky="w", padx=6)

        self.check_button = ttk.Button(
            settings, text="Check connection", command=self.check_connection
        )
        self.check_button.grid(row=0, column=5, sticky="ew", padx=(6, 0))

        ttk.Label(settings, text="Model:").grid(
            row=1, column=0, sticky="w", padx=(0, 6), pady=4
        )
        self.model_box = ttk.Combobox(
            settings, textvariable=self.model_var, width=48
        )
        self.model_box.grid(row=1, column=1, columnspan=2, sticky="ew", pady=4)

        ttk.Label(settings, text="Output folder:").grid(
            row=1, column=3, sticky="e", padx=(12, 6), pady=4
        )
        ttk.Entry(settings, textvariable=self.output_var, width=45).grid(
            row=1, column=4, sticky="ew", pady=4
        )
        ttk.Button(settings, text="Browse", command=self.browse_output).grid(
            row=1, column=5, sticky="ew", padx=(6, 0)
        )

        ttk.Label(settings, text="Folders:").grid(
            row=2, column=0, sticky="w", padx=(0, 6), pady=4
        )
        ttk.Spinbox(
            settings, from_=1, to=100, textvariable=self.folder_count_var, width=8
        ).grid(row=2, column=1, sticky="w", pady=4)

        ttk.Label(settings, text="Files per folder:").grid(
            row=2, column=2, sticky="e", padx=(12, 6), pady=4
        )
        ttk.Spinbox(
            settings,
            from_=1,
            to=10000,
            textvariable=self.files_per_folder_var,
            width=10,
        ).grid(row=2, column=3, sticky="w", pady=4)

        ttk.Label(settings, text="Simultaneous makers:").grid(
            row=2, column=4, sticky="e", padx=(12, 6), pady=4
        )
        ttk.Spinbox(
            settings, from_=1, to=50, textvariable=self.worker_count_var, width=8
        ).grid(row=2, column=5, sticky="w", pady=4)

        ttk.Label(settings, text="Requests per minute (0 = unlimited):").grid(
            row=3, column=0, sticky="w", padx=(0, 6), pady=4
        )
        ttk.Spinbox(
            settings, from_=0, to=10000, textvariable=self.rpm_var, width=8
        ).grid(row=3, column=1, sticky="w", pady=4)

        ttk.Label(settings, text="Retries per file:").grid(
            row=3, column=2, sticky="e", padx=(12, 6), pady=4
        )
        ttk.Spinbox(
            settings, from_=1, to=20, textvariable=self.retries_var, width=8
        ).grid(row=3, column=3, sticky="w", pady=4)

        self.provider_help_var = tk.StringVar()
        ttk.Label(settings, textvariable=self.provider_help_var).grid(
            row=3, column=4, columnspan=2, sticky="w", padx=(12, 0)
        )

        settings.columnconfigure(1, weight=1)
        settings.columnconfigure(3, weight=1)
        settings.columnconfigure(4, weight=1)

        controls = ttk.Frame(outer)
        controls.pack(fill="x", pady=(10, 8))

        self.start_button = ttk.Button(
            controls, text="Start makers", command=self.start_generation
        )
        self.start_button.pack(side="left")

        self.stop_button = ttk.Button(
            controls, text="Stop", command=self.stop_generation, state="disabled"
        )
        self.stop_button.pack(side="left", padx=8)

        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(controls, textvariable=self.status_var).pack(side="left", padx=12)

        self.summary_var = tk.StringVar(value="Created: 0   Skipped: 0   Failed: 0")
        ttk.Label(controls, textvariable=self.summary_var).pack(side="right")

        self.progress = ttk.Progressbar(outer, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(0, 4))

        self.time_remaining_var = tk.StringVar(
            value="Estimated time remaining: --   Elapsed: 0s"
        )
        ttk.Label(
            outer,
            textvariable=self.time_remaining_var,
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w", pady=(0, 10))

        folder_frame = ttk.LabelFrame(outer, text="Folder makers", padding=6)
        folder_frame.pack(fill="both", expand=True)

        columns = ("folder", "status", "current", "created", "skipped", "failed")
        self.tree = ttk.Treeview(
            folder_frame, columns=columns, show="headings", height=13
        )

        headings = {
            "folder": "Folder",
            "status": "Maker status",
            "current": "Current file",
            "created": "Created",
            "skipped": "Skipped",
            "failed": "Failed",
        }
        widths = {
            "folder": 120,
            "status": 190,
            "current": 180,
            "created": 85,
            "skipped": 85,
            "failed": 85,
        }

        for column in columns:
            self.tree.heading(column, text=headings[column])
            self.tree.column(
                column,
                width=widths[column],
                anchor="center" if column != "status" else "w",
            )

        tree_scroll = ttk.Scrollbar(
            folder_frame, orient="vertical", command=self.tree.yview
        )
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        tree_scroll.pack(side="right", fill="y")

        log_frame = ttk.LabelFrame(outer, text="Activity log", padding=6)
        log_frame.pack(fill="both", expand=True, pady=(10, 0))

        self.log_text = tk.Text(log_frame, height=10, wrap="word", state="disabled")
        log_scroll = ttk.Scrollbar(
            log_frame, orient="vertical", command=self.log_text.yview
        )
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

    def provider_changed(self, first_time=False):
        name = self.provider_var.get()
        provider = PROVIDERS[name]
        self.key_label.configure(text=provider["key_label"])
        self.model_var.set(provider["default_model"])
        self.worker_count_var.set(provider["default_workers"])
        self.rpm_var.set(provider["default_rpm"])
        self.model_box["values"] = ()

        if name == "LM Studio":
            self.provider_help_var.set(
                "Start with 1 maker; try 2 only after it works. No cloud API key is needed by default."
            )
            self.status_var.set("LM Studio selected — start its local server, then check connection")
        else:
            self.provider_help_var.set(
                "All makers share the same OpenRouter account limits."
            )
            self.status_var.set("OpenRouter selected")

        if not first_time:
            self.log(f"Provider changed to {name}.")

    def toggle_key(self):
        self.key_entry.configure(show="" if self.show_key_var.get() else "*")

    def browse_output(self):
        selected = filedialog.askdirectory(
            title="Choose the main output folder",
            initialdir=self.output_var.get() or os.path.expanduser("~"),
        )
        if selected:
            self.output_var.set(selected)

    def log(self, message):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message.rstrip() + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def build_headers(self, provider_name, api_key):
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        if provider_name == "OpenRouter":
            headers["HTTP-Referer"] = "http://localhost"
            headers["X-Title"] = "ICA Multi-Maker Generator"
        return headers

    def check_connection(self):
        provider_name = self.provider_var.get()
        provider = PROVIDERS[provider_name]
        api_key = self.api_key_var.get().strip()

        if provider["requires_key"] and not api_key:
            messagebox.showerror("Missing key", "Paste your OpenRouter API key first.")
            return

        self.status_var.set(f"Checking {provider_name}...")
        self.check_button.configure(state="disabled")

        def worker():
            try:
                response = requests.get(
                    provider["models_url"],
                    headers=self.build_headers(provider_name, api_key),
                    timeout=30,
                )

                if response.status_code != 200:
                    details = self.extract_error(response)
                    if provider_name == "LM Studio" and response.status_code == 401:
                        details = (
                            "LM Studio authentication is enabled. Either turn off "
                            "Developer > Server Settings > Require Authentication, "
                            "or create an LM Studio API token and paste it into the token box.\n\n"
                            f"Server message: {details}"
                        )
                    self.events.put(("connection_result", False, provider_name, details, []))
                    return

                result = response.json()
                models = [
                    item.get("id", "").strip()
                    for item in result.get("data", [])
                    if isinstance(item, dict) and item.get("id")
                ]

                if not models:
                    self.events.put((
                        "connection_result",
                        False,
                        provider_name,
                        "The server answered, but no models were listed. Download/load a model in LM Studio.",
                        [],
                    ))
                    return

                self.events.put((
                    "connection_result",
                    True,
                    provider_name,
                    f"Connected successfully. Found {len(models)} model(s).",
                    models,
                ))

            except requests.ConnectionError:
                self.events.put((
                    "connection_result",
                    False,
                    provider_name,
                    "Could not connect. In LM Studio, open Developer and start the server on port 1234.",
                    [],
                ))
            except requests.Timeout:
                self.events.put((
                    "connection_result",
                    False,
                    provider_name,
                    "The connection check timed out.",
                    [],
                ))
            except requests.RequestException as error:
                self.events.put(("connection_result", False, provider_name, str(error), []))
            except ValueError as error:
                self.events.put((
                    "connection_result",
                    False,
                    provider_name,
                    f"The server returned invalid JSON: {error}",
                    [],
                ))

        threading.Thread(target=worker, daemon=True).start()

    def validate_settings(self):
        provider_name = self.provider_var.get()
        provider = PROVIDERS[provider_name]
        api_key = self.api_key_var.get().strip()
        model = self.model_var.get().strip()
        output = self.output_var.get().strip()

        if provider["requires_key"] and not api_key:
            raise ValueError("Paste your OpenRouter API key.")
        if not model:
            raise ValueError("Choose or enter a model name. Use Check connection to load the model list.")
        if not output:
            raise ValueError("Choose an output folder.")

        try:
            folders = int(self.folder_count_var.get())
            files_per_folder = int(self.files_per_folder_var.get())
            workers = int(self.worker_count_var.get())
            rpm = int(self.rpm_var.get())
            retries = int(self.retries_var.get())
        except ValueError as error:
            raise ValueError("All number settings must be whole numbers.") from error

        if folders < 1 or files_per_folder < 1:
            raise ValueError("Folders and files per folder must be at least 1.")
        if workers < 1:
            raise ValueError("Simultaneous makers must be at least 1.")
        if rpm < 0:
            raise ValueError("Requests per minute cannot be negative.")
        if retries < 1:
            raise ValueError("Retries must be at least 1.")

        workers = min(workers, folders)

        return {
            "provider_name": provider_name,
            "api_url": provider["api_url"],
            "api_key": api_key,
            "model": model,
            "output": output,
            "folders": folders,
            "files_per_folder": files_per_folder,
            "workers": workers,
            "rpm": rpm,
            "retries": retries,
        }

    def start_generation(self):
        if self.running:
            return

        try:
            config = self.validate_settings()
        except ValueError as error:
            messagebox.showerror("Invalid settings", str(error))
            return

        if config["provider_name"] == "LM Studio" and config["workers"] > 2:
            proceed = messagebox.askyesno(
                "High local concurrency",
                "More than two makers may be slower or may run out of RAM/VRAM because all makers share one local model. Start anyway?",
            )
            if not proceed:
                return

        total = config["folders"] * config["files_per_folder"]

        self.stop_event.clear()
        self.running = True
        self.created = 0
        self.skipped = 0
        self.failed = 0
        self.finished_items = 0
        self.total_items = total
        self.run_started_at = time.monotonic()
        self.progress["value"] = 0
        self.progress["maximum"] = max(1, total)
        self.summary_var.set("Created: 0   Skipped: 0   Failed: 0")
        self.time_remaining_var.set(
            "Estimated time remaining: calculating...   Elapsed: 0s"
        )

        for item in self.tree.get_children():
            self.tree.delete(item)
        self.folder_items.clear()

        for folder_num in range(1, config["folders"] + 1):
            folder_name = f"ica{folder_num}"
            item = self.tree.insert(
                "", "end", values=(folder_name, "Waiting", "-", 0, 0, 0)
            )
            self.folder_items[folder_num] = item

        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        rate_text = "unlimited starts" if config["rpm"] == 0 else f"up to {config['rpm']} requests/minute"
        self.status_var.set(
            f"Running {config['workers']} maker(s) through {config['provider_name']} — {rate_text}"
        )
        self.log(
            f"Starting {config['folders']} folder makers, {config['files_per_folder']} files each, using {config['provider_name']}."
        )

        threading.Thread(
            target=self.run_all_folders, args=(config,), daemon=True
        ).start()

    def stop_generation(self):
        if self.running:
            self.stop_event.set()
            self.status_var.set("Stopping after active requests finish...")
            self.stop_button.configure(state="disabled")
            self.log("Stop requested.")

    def run_all_folders(self, config):
        limiter = GlobalRateLimiter(config["rpm"])

        with ThreadPoolExecutor(max_workers=config["workers"]) as executor:
            futures = [
                executor.submit(self.run_folder, folder_num, config, limiter)
                for folder_num in range(1, config["folders"] + 1)
            ]

            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as error:
                    self.events.put(("log", f"Unexpected maker error: {error}"))

        self.events.put(("all_done", self.stop_event.is_set()))

    def run_folder(self, folder_num, config, limiter):
        folder_name = f"ica{folder_num}"
        folder_path = os.path.join(config["output"], folder_name)

        local_created = 0
        local_skipped = 0
        local_failed = 0

        self.events.put((
            "folder_update", folder_num, "Running", "-",
            local_created, local_skipped, local_failed,
        ))

        for file_num in range(1, config["files_per_folder"] + 1):
            if self.stop_event.is_set():
                break

            padded_file = f"{file_num:03d}"
            global_profile = (
                (folder_num - 1) * config["files_per_folder"] + file_num
            )
            padded_profile = f"{global_profile:04d}"

            file_name = f"kill order-{padded_file}.txt"
            file_path = os.path.join(folder_path, file_name)

            self.events.put((
                "folder_update", folder_num, "Generating", file_name,
                local_created, local_skipped, local_failed,
            ))

            if os.path.isfile(file_path) and os.path.getsize(file_path) > 0:
                local_skipped += 1
                self.record_result("skipped")
                self.events.put((
                    "folder_update", folder_num, "Running", file_name,
                    local_created, local_skipped, local_failed,
                ))
                continue

            name = f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"
            alias = random.choice(ALIASES)
            location = random.choice(LOCATIONS)
            net_worth = self.random_net_worth()
            prompt = self.create_prompt(
                padded_profile, name, alias, location, net_worth
            )

            dossier, error = self.request_dossier(config, limiter, prompt)

            if dossier is None:
                if self.stop_event.is_set():
                    break
                local_failed += 1
                self.record_result("failed")
                self.events.put((
                    "log", f"{folder_name}\\{file_name} failed: {error}"
                ))
            else:
                try:
                    os.makedirs(folder_path, exist_ok=True)
                    with open(file_path, "w", encoding="utf-8") as output_file:
                        output_file.write(dossier)
                    local_created += 1
                    self.record_result("created")
                    self.events.put(("log", f"Saved {folder_name}\\{file_name}"))
                except OSError as error:
                    local_failed += 1
                    self.record_result("failed")
                    self.events.put((
                        "log", f"Could not save {folder_name}\\{file_name}: {error}"
                    ))

            self.events.put((
                "folder_update", folder_num, "Running", file_name,
                local_created, local_skipped, local_failed,
            ))

        final_status = "Stopped" if self.stop_event.is_set() else "Finished"
        self.events.put((
            "folder_update", folder_num, final_status, "-",
            local_created, local_skipped, local_failed,
        ))

    def request_dossier(self, config, limiter, prompt):
        headers = self.build_headers(config["provider_name"], config["api_key"])
        payload = {
            "model": config["model"],
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7,
            "max_tokens": 1500,
        }

        last_error = "Unknown error"

        for attempt in range(1, config["retries"] + 1):
            if self.stop_event.is_set():
                return None, "Stopped"

            if not limiter.wait(self.stop_event):
                return None, "Stopped"

            try:
                response = requests.post(
                    config["api_url"],
                    headers=headers,
                    json=payload,
                    timeout=300,
                )

                if response.status_code == 200:
                    result = response.json()
                    choices = result.get("choices") or []
                    if not choices:
                        return None, "The API response contained no choices."

                    content = (
                        choices[0].get("message", {}).get("content", "")
                    ).strip()
                    if not content:
                        return None, "The model returned an empty message."
                    return content, None

                last_error = self.extract_error(response)

                if response.status_code == 401 and config["provider_name"] == "LM Studio":
                    return None, (
                        "LM Studio requires authentication. Turn off Require Authentication "
                        "or paste an LM Studio API token into the token box."
                    )

                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    try:
                        wait_seconds = float(retry_after)
                    except (TypeError, ValueError):
                        wait_seconds = min(60, attempt * 15)
                    self.events.put((
                        "log", f"Rate limited. Waiting {wait_seconds:.0f} seconds."
                    ))
                    if self.stop_event.wait(wait_seconds):
                        return None, "Stopped"
                    continue

                if response.status_code in (500, 502, 503, 504, 529):
                    wait_seconds = min(60, attempt * 10)
                    self.events.put((
                        "log",
                        f"Provider error {response.status_code}. Retrying in {wait_seconds} seconds.",
                    ))
                    if self.stop_event.wait(wait_seconds):
                        return None, "Stopped"
                    continue

                return None, f"HTTP {response.status_code}: {last_error}"

            except requests.Timeout:
                last_error = "Request timed out. A local model may still be loading or generating."
            except requests.ConnectionError:
                last_error = "Connection failed. Make sure the LM Studio server is running on port 1234."
            except requests.RequestException as error:
                last_error = f"Request error: {error}"
            except (ValueError, KeyError, IndexError) as error:
                return None, f"Invalid API response: {error}"

            if attempt < config["retries"]:
                wait_seconds = min(30, attempt * 5)
                if self.stop_event.wait(wait_seconds):
                    return None, "Stopped"

        return None, last_error

    @staticmethod
    def extract_error(response):
        try:
            data = response.json()
            error = data.get("error")
            if isinstance(error, dict):
                return str(error.get("message", error))
            if error:
                return str(error)
            return str(data)
        except ValueError:
            return response.text[:800]

    def record_result(self, result_type):
        with self.counter_lock:
            if result_type == "created":
                self.created += 1
            elif result_type == "skipped":
                self.skipped += 1
            else:
                self.failed += 1

            self.finished_items += 1
            snapshot = (
                self.created, self.skipped, self.failed, self.finished_items
            )

        self.events.put(("progress", *snapshot))

    @staticmethod
    def random_net_worth():
        if random.choice([True, False]):
            return f"${random.randint(50, 950)} Million"
        return f"${round(random.uniform(1.1, 8.4), 1)} Billion"

    @staticmethod
    def create_prompt(profile_number, name, alias, location, net_worth):
        archive_alias = alias.upper().replace(" ", "")
        return (
            "Write a fictional, gritty intelligence dossier for a "
            "video-game-style criminal-syndicate target. All people, "
            "organizations, events, and allegations must be fictional.\n\n"
            "Return only the completed dossier. Do not include markdown code "
            "fences, an introduction, warnings, explanations, or notes.\n\n"
            "Use this exact structure:\n\n"
            "===============================================================================\n"
            f"ICA CLASSIFIED DOSSIER // PROFILE #{profile_number}\n"
            "===============================================================================\n"
            f"SUBJECT NAME:      {name}\n"
            f"KNOWN ALIAS:       {alias}\n"
            f"LAST KNOWN LOC:    {location}\n"
            f"EST. NET WORTH:    {net_worth}\n"
            "THREAT LEVEL:      CRITICAL (CLASS-A)\n"
            "===============================================================================\n\n"
            "1. CRIMINAL ENTERPRISE & OPERATIONS\n"
            "-------------------------------------------------------------------------------\n"
            "Write detailed fictional paragraphs explaining the subject's "
            "criminal business empire, sources of income, front companies, "
            "influence, and international operations.\n\n"
            "2. KNOWN CRIMINAL ASSOCIATES\n"
            "-------------------------------------------------------------------------------\n"
            "List two or three completely fictional associates. Give each a "
            "full name and a brief description of their role.\n\n"
            "3. PSYCHOLOGICAL PROFILE & BEHAVIORAL ANALYSIS\n"
            "-------------------------------------------------------------------------------\n"
            "Write a fictional behavioral analysis covering habits, flaws, "
            "decision-making patterns, fears, and unusual lifestyle patterns.\n\n"
            "===============================================================================\n"
            f"FILE STATUS: ACTIVE // ARCHIVE REFERENCE: ICA-{profile_number}-{archive_alias}\n"
            "===============================================================================\n"
        )

    @staticmethod
    def format_duration(seconds):
        """Format seconds as a compact days/hours/minutes/seconds string."""
        seconds = max(0, int(round(seconds)))
        days, seconds = divmod(seconds, 86400)
        hours, seconds = divmod(seconds, 3600)
        minutes, seconds = divmod(seconds, 60)

        parts = []
        if days:
            parts.append(f"{days}d")
        if hours or days:
            parts.append(f"{hours}h")
        if minutes or hours or days:
            parts.append(f"{minutes}m")
        parts.append(f"{seconds}s")
        return " ".join(parts)

    def update_time_display(self):
        """Refresh elapsed time and the live ETA once per second."""
        if self.running and self.run_started_at is not None:
            elapsed = max(0.0, time.monotonic() - self.run_started_at)

            with self.counter_lock:
                finished = self.finished_items
                total = self.total_items

            remaining_items = max(0, total - finished)

            if remaining_items == 0:
                remaining_text = "0s"
            elif finished <= 0 or elapsed < 0.5:
                remaining_text = "calculating..."
            else:
                items_per_second = finished / elapsed
                if items_per_second <= 0:
                    remaining_text = "calculating..."
                else:
                    eta_seconds = remaining_items / items_per_second
                    remaining_text = self.format_duration(eta_seconds)

            self.time_remaining_var.set(
                "Estimated time remaining: "
                f"{remaining_text}   Elapsed: {self.format_duration(elapsed)}"
                f"   Remaining files: {remaining_items:,}"
            )

        self.root.after(1000, self.update_time_display)

    def process_events(self):
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]

                if kind == "log":
                    self.log(event[1])

                elif kind == "progress":
                    _, created, skipped, failed, finished = event
                    self.progress["value"] = finished
                    self.summary_var.set(
                        f"Created: {created}   Skipped: {skipped}   Failed: {failed}"
                    )

                    if self.running and self.run_started_at is not None:
                        elapsed = max(
                            0.0, time.monotonic() - self.run_started_at
                        )
                        remaining_items = max(
                            0, self.total_items - finished
                        )
                        if finished > 0 and elapsed > 0:
                            eta_seconds = remaining_items / (finished / elapsed)
                            eta_text = self.format_duration(eta_seconds)
                        else:
                            eta_text = "calculating..."
                        self.time_remaining_var.set(
                            f"Estimated time remaining: {eta_text}   "
                            f"Elapsed: {self.format_duration(elapsed)}   "
                            f"Remaining files: {remaining_items:,}"
                        )

                elif kind == "folder_update":
                    (
                        _, folder_num, status, current,
                        created, skipped, failed,
                    ) = event
                    item = self.folder_items.get(folder_num)
                    if item:
                        self.tree.item(
                            item,
                            values=(
                                f"ica{folder_num}", status, current,
                                created, skipped, failed,
                            ),
                        )

                elif kind == "connection_result":
                    _, success, provider_name, details, models = event
                    self.check_button.configure(state="normal")
                    self.status_var.set("Ready")
                    if success:
                        self.model_box["values"] = models
                        if not self.model_var.get().strip() or self.model_var.get() not in models:
                            self.model_var.set(models[0])
                        self.log(f"{provider_name}: {details}")
                        messagebox.showinfo("Connection successful", details)
                    else:
                        self.log(f"{provider_name} connection failed: {details}")
                        messagebox.showerror("Connection failed", details)

                elif kind == "all_done":
                    _, was_stopped = event
                    elapsed = (
                        max(0.0, time.monotonic() - self.run_started_at)
                        if self.run_started_at is not None
                        else 0.0
                    )
                    self.running = False
                    self.start_button.configure(state="normal")
                    self.stop_button.configure(state="disabled")

                    with self.counter_lock:
                        remaining_items = max(
                            0, self.total_items - self.finished_items
                        )

                    if was_stopped:
                        self.status_var.set("Stopped")
                        self.time_remaining_var.set(
                            f"Stopped after: {self.format_duration(elapsed)}"
                            f"   Unfinished files: {remaining_items:,}"
                        )
                        self.log("Generation stopped.")
                    else:
                        self.status_var.set("Finished")
                        self.time_remaining_var.set(
                            "Estimated time remaining: 0s   "
                            f"Total time: {self.format_duration(elapsed)}"
                        )
                        self.log("All folder makers finished.")

        except queue.Empty:
            pass

        self.root.after(100, self.process_events)

    def on_close(self):
        if self.running:
            close = messagebox.askyesno(
                "Generation running", "Stop the makers and close the program?"
            )
            if not close:
                return
            self.stop_event.set()
        self.root.destroy()


def main():
    root = tk.Tk()
    ICAGeneratorGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
