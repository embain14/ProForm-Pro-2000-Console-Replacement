import tkinter as tk
from tkinter import Toplevel, messagebox
import time
import os
from gpiozero import PWMOutputDevice, DigitalOutputDevice, DigitalInputDevice
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib.pyplot as plt
from collections import deque

# --- CONFIGURATION ---
BELT_LENGTH_FEET = 10.83
MIN_DUTY = 20.0
MAX_DUTY = 85.0

# --- HARDWARE SETUP ---
motor = PWMOutputDevice(18, frequency=20, initial_value=0)
inc_up_pin = DigitalOutputDevice(24)
inc_down_pin = DigitalOutputDevice(25)
oe_pin = DigitalOutputDevice(23, initial_value=False)
inc_sense = DigitalInputDevice(22, pull_up=False)

# --- TOUCH NUMPAD ---
class TouchNumpad(Toplevel):
    """Compact fullscreen numeric keypad."""

    def __init__(self, parent, title="Enter Value"):
        super().__init__(parent)
        self.configure(bg="#1e1e1e")

        # FULLSCREEN / TOPMOST safe for touchscreen
        self.attributes("-fullscreen", True)
        self.attributes("-topmost", True)
        self.config(cursor="none")

        # Force proper fullscreen geometry
        self.update_idletasks()
        self.geometry(f"{self.winfo_screenwidth()}x{self.winfo_screenheight()}+0+0")

        self.result = None

        tk.Label(self, text=title, font=("Arial", 24, "bold"),
                 bg="#1e1e1e", fg="#3498db").pack(pady=10)

        self.entry_var = tk.StringVar()
        entry = tk.Entry(self, textvariable=self.entry_var,
                         font=("Arial", 40), justify="center",
                         bg="#333", fg="white", bd=0)
        entry.pack(pady=5, padx=100, fill="x")

        grid_frame = tk.Frame(self, bg="#1e1e1e")
        grid_frame.pack(expand=True, fill="both", padx=10, pady=10)

        for i in range(5):
            grid_frame.rowconfigure(i, weight=1)
        for i in range(3):
            grid_frame.columnconfigure(i, weight=1)

        buttons = [
            ('7', 0, 0), ('8', 0, 1), ('9', 0, 2),
            ('4', 1, 0), ('5', 1, 1), ('6', 1, 2),
            ('1', 2, 0), ('2', 2, 1), ('3', 2, 2),
            ('.', 3, 0), ('0', 3, 1), ('DEL', 3, 2)
        ]
        for (text, r, c) in buttons:
            color = "#444" if text != "DEL" else "#e74c3c"
            tk.Button(grid_frame, text=text, font=("Arial", 26, "bold"),
                      bg=color, fg="white",
                      command=lambda x=text: self.click(x)).grid(row=r, column=c, sticky="nsew", padx=5, pady=5)

        tk.Button(grid_frame, text="CONFIRM", font=("Arial", 30, "bold"),
                  bg="#2ecc71", fg="white", command=self.submit).grid(row=4, column=0, columnspan=3,
                                                                      sticky="nsew", padx=5, pady=10)
        self.grab_set()
        self.wait_window()

    def click(self, char):
        if char == "DEL":
            self.entry_var.set(self.entry_var.get()[:-1])
        else:
            self.entry_var.set(self.entry_var.get() + char)

    def submit(self):
        try:
            self.result = float(self.entry_var.get())
            self.destroy()
        except ValueError:
            self.entry_var.set("")


# --- MAIN APP ---
class TreadmillTopControl:
    def __init__(self, root):
        self.root = root
        self.root.title("Treadmill OS")
        self.root.configure(bg="#121212")

        # Fullscreen kiosk
        self.root.attributes("-fullscreen", True)
        self.root.attributes("-topmost", True)
        self.root.config(cursor="none")
        self.root.update_idletasks()
        self.root.after(100, lambda: self.root.update())

        # Treadmill state
        self.target_duty = MIN_DUTY
        self.current_duty = 0.0
        self.is_paused = True
        self.is_counting_down = False
        self.user_weight_kg = 86.0
        self.incline_percent = 0.0
        self.total_seconds = 0
        self.calories = 0.0
        self.total_miles = 0.0
        self.speed_history = deque([0.0]*60, maxlen=60)

        self.setup_ui()
        self.update_loop()
        self.ramp_loop()

    def setup_ui(self):
        # --- Top Power Bar ---
        power_bar = tk.Frame(self.root, bg="#121212")
        power_bar.pack(side="top", anchor="ne", padx=10, pady=5)

        tk.Button(power_bar, text="EXIT APP", font=("Arial", 10, "bold"),
                  bg="#444", fg="white", command=self.on_close).pack(side="left", padx=5)
        tk.Button(power_bar, text="SHUTDOWN PI", font=("Arial", 10, "bold"),
                  bg="#900", fg="white", command=self.shutdown_pi).pack(side="left", padx=5)

        # --- Control Bar ---
        ctrl_bar = tk.Frame(self.root, bg="#252525", pady=10)
        ctrl_bar.pack(side="top", fill="x")
        ctrl_bar.columnconfigure((0, 1, 2), weight=1)

        speed_f = tk.Frame(ctrl_bar, bg="#252525")
        speed_f.grid(row=0, column=0)
        tk.Button(speed_f, text="MPH -", width=8, height=3,
                  command=lambda: self.adj_speed(-1)).pack(side="left", padx=5)
        tk.Button(speed_f, text="MPH +", width=8, height=3,
                  command=lambda: self.adj_speed(1)).pack(side="left", padx=5)

        act_f = tk.Frame(ctrl_bar, bg="#252525")
        act_f.grid(row=0, column=1)
        self.btn_pause = tk.Button(act_f, text="START", bg="#2ecc71", fg="white",
                                   width=12, height=3, font=("Arial", 12, "bold"),
                                   command=self.toggle_pause)
        self.btn_pause.pack(side="left", padx=5)
        tk.Button(act_f, text="STOP", bg="#e74c3c", fg="white",
                  width=12, height=3, font=("Arial", 12, "bold"),
                  command=self.emergency_stop).pack(side="left", padx=5)

        inc_f = tk.Frame(ctrl_bar, bg="#252525")
        inc_f.grid(row=0, column=2)
        btn_up = tk.Button(inc_f, text="INC ▲", width=8, height=3)
        btn_up.pack(side="left", padx=5)
        btn_up.bind("<Button-1>", lambda e: self.safe_inc_on(inc_up_pin))
        btn_up.bind("<ButtonRelease-1>", lambda e: inc_up_pin.off())
        btn_dn = tk.Button(inc_f, text="INC ▼", width=8, height=3)
        btn_dn.pack(side="left", padx=5)
        btn_dn.bind("<Button-1>", lambda e: self.safe_inc_on(inc_down_pin))
        btn_dn.bind("<ButtonRelease-1>", lambda e: inc_down_pin.off())

        # --- Stats Row ---
        stats_frame = tk.Frame(self.root, bg="#121212", pady=25)
        stats_frame.pack(side="top", fill="x")
        stats_frame.columnconfigure((0, 1, 2, 3, 4), weight=1)

        self.lbl_speed = self.add_stat(stats_frame, "MPH", "0.0", "#00d1b2", 0)
        self.lbl_pace = self.add_stat(stats_frame, "PACE", "--:--", "#9b59b6", 1)
        self.lbl_dist = self.add_stat(stats_frame, "MILES", "0.00", "#3498db", 2)
        self.lbl_cals = self.add_stat(stats_frame, "KCAL", "0", "#ff3860", 3)
        self.lbl_time = self.add_stat(stats_frame, "TIME", "00:00", "#ffffff", 4)

        # --- Graph ---
        graph_f = tk.Frame(self.root, bg="#121212")
        graph_f.pack(side="top", fill="both", expand=True)
        self.fig, self.ax_mph = plt.subplots(figsize=(8, 5), dpi=100, facecolor='#121212')
        self.ax_mph.set_facecolor('#1e1e1e')
        self.ax_mph.tick_params(axis='both', colors='white')
        self.ln_speed, = self.ax_mph.plot(range(60), self.speed_history, color="#00d1b2", lw=3)
        self.ax_mph.set_ylim(0, 12)
        self.canvas = FigureCanvasTkAgg(self.fig, master=graph_f)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

    def add_stat(self, parent, title, val, color, col):
        f = tk.Frame(parent, bg="#121212")
        f.grid(row=0, column=col)
        tk.Label(f, text=title, fg="gray", bg="#121212", font=("Arial", 12)).pack()
        l = tk.Label(f, text=val, fg=color, bg="#121212", font=("Arial", 38, "bold"))
        l.pack()
        return l

    # --- LOGIC FUNCTIONS ---
    def duty_to_mph(self, duty):
        if duty < MIN_DUTY: return 0.0
        revs_per_min = 1.4 * (duty - 20) + 8
        return (revs_per_min * BELT_LENGTH_FEET * 60) / 5280

    def safe_inc_on(self, pin):
        if oe_pin.value: pin.on()

    def adj_speed(self, delta):
        if not self.is_counting_down:
            self.target_duty = max(MIN_DUTY, min(MAX_DUTY, self.target_duty + delta))

    def toggle_pause(self):
        if self.is_paused and self.total_seconds == 0:
            w_pad = TouchNumpad(self.root, "Weight (lbs)")
            if w_pad.result: self.user_weight_kg = w_pad.result * 0.453592
            else: return
            i_pad = TouchNumpad(self.root, "Incline % (0-15)")
            if i_pad.result is not None: self.incline_percent = i_pad.result / 100.0
            else: return
            self.start_countdown(3)
            return
        self.is_paused = not self.is_paused
        self.btn_pause.config(text="RESUME" if self.is_paused else "PAUSE",
                              bg="#f1c40f" if self.is_paused else "#3498db")
        if not self.is_paused: oe_pin.on()

    def start_countdown(self, seconds):
        self.is_counting_down = True
        self.btn_pause.config(state="disabled", text="WAIT...", bg="#444")
        if seconds > 0:
            self.lbl_speed.config(text=f"{seconds}...")
            self.root.after(1000, lambda: self.start_countdown(seconds - 1))
        else:
            self.is_counting_down = False
            self.is_paused = False
            oe_pin.on()
            self.btn_pause.config(state="normal", text="PAUSE", bg="#3498db")

    def emergency_stop(self):
        self.is_counting_down = False
        self.target_duty = MIN_DUTY
        self.current_duty = 0
        motor.value = 0
        oe_pin.off()
        self.is_paused = True
        self.total_seconds = 0
        self.calories = 0
        self.total_miles = 0.0
        self.btn_pause.config(text="START", bg="#2ecc71", state="normal")

    def ramp_loop(self):
        actual_target = 0 if (self.is_paused or self.is_counting_down) else self.target_duty / 100.0
        step = 0.005
        if abs(self.current_duty - actual_target) > step:
            self.current_duty += step if self.current_duty < actual_target else -step
        else:
            self.current_duty = actual_target
        motor.value = self.current_duty
        self.root.after(50, self.ramp_loop)

    def update_loop(self):
        mph = self.duty_to_mph(self.current_duty * 100)
        if not self.is_counting_down: self.lbl_speed.config(text=f"{mph:.1f}")
        if mph > 0.5:
            dp = 60.0 / mph
            self.lbl_pace.config(text=f"{int(dp):02d}:{int((dp%1)*60):02d}")
        else:
            self.lbl_pace.config(text="--:--")
        if not self.is_paused:
            self.total_seconds += 1
            self.total_miles += mph / 3600.0
            m_min = mph * 26.8224
            vo2 = (0.2*m_min) + (0.9*m_min*self.incline_percent) + 3.5
            self.calories += ((vo2*self.user_weight_kg)/200.0)/60.0
            self.speed_history.append(mph)
            self.lbl_time.config(text=f"{self.total_seconds//60:02d}:{self.total_seconds%60:02d}")
            self.lbl_cals.config(text=f"{int(self.calories)}")
            self.lbl_dist.config(text=f"{self.total_miles:.2f}")
            self.ln_speed.set_ydata(self.speed_history)
            self.canvas.draw_idle()
        self.root.after(1000, self.update_loop)

    def shutdown_pi(self):
        if messagebox.askyesno("Shutdown", "Do you want to shut down the computer?"):
            self.on_close()
            os.system("sudo shutdown -h now")

    def on_close(self):
        motor.close()
        oe_pin.off()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = TreadmillTopControl(root)
    root.mainloop()
