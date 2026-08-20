"""Tkinter 기반의 기존 데스크톱 설정·분석 화면.

통합 대시보드가 기본 실행 경로지만, ROI 설정과 로컬 단독 분석을 위해
이 GUI도 유지한다. 실제 영상 처리는 :class:`BatchAnalyzer`에 위임한다.
"""

from __future__ import annotations

import copy
import os
import threading
import traceback
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from calibrate import calibrate, find_day_video
from two_way_counter import BatchAnalyzer, default_config, load_config, save_config


APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.json"
DEFAULT_SOURCE = ""


class CounterApp(tk.Tk):
    """분석 설정을 편집하고 백그라운드 분석 스레드를 제어하는 창."""

    def __init__(self) -> None:
        """설정 파일을 읽고 화면 상태 변수와 위젯을 초기화한다."""
        super().__init__()
        self.title("2way 차량 추적")
        self.geometry("900x720")
        self.minsize(760, 620)
        self.configure(bg="#eef3f1")
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.config_data = self.ensure_config()

        self.source_var = tk.StringVar(value=self.config_data["source_dir"])
        self.output_var = tk.StringVar(value=self.config_data.get("output_dir", "results"))
        self.model_var = tk.StringVar(value=self.config_data.get("model", "yolo26s.pt"))
        self.device_var = tk.StringVar(value=self.config_data.get("device", "0"))
        self.conf_var = tk.StringVar(value=str(self.config_data.get("confidence", 0.22)))
        self.detailed_records_var = tk.BooleanVar(
            value=self.config_data.get("save_detailed_records", True)
        )
        self.event_images_var = tk.BooleanVar(value=self.config_data.get("save_event_images", True))
        self.review_video_var = tk.BooleanVar(value=self.config_data.get("save_review_video", False))
        self.progress_var = tk.DoubleVar(value=0)
        self.status_var = tk.StringVar(value="영역을 확인한 뒤 테스트 분석을 실행하세요.")
        self.direction_var = tk.StringVar()
        self.build_style()
        self.build_ui()
        self.refresh_direction_text()

    def ensure_config(self) -> dict:
        """설정 파일이 없으면 기본값으로 만든 뒤 현재 설정을 반환한다."""
        if not CONFIG_PATH.exists():
            save_config(CONFIG_PATH, default_config(DEFAULT_SOURCE))
        return load_config(CONFIG_PATH)

    def build_style(self) -> None:
        """전체 화면에서 공유하는 ttk 색상과 글꼴 스타일을 정의한다."""
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background="#eef3f1")
        style.configure("Card.TFrame", background="#ffffff")
        style.configure("TLabel", background="#eef3f1", foreground="#17352c", font=("맑은 고딕", 10))
        style.configure("Title.TLabel", background="#eef3f1", foreground="#17352c", font=("맑은 고딕", 23, "bold"))
        style.configure("Subtitle.TLabel", background="#eef3f1", foreground="#667a72", font=("맑은 고딕", 10))
        style.configure("Card.TLabel", background="#ffffff", foreground="#17352c", font=("맑은 고딕", 10))
        style.configure("Section.TLabel", background="#ffffff", foreground="#17352c", font=("맑은 고딕", 12, "bold"))
        style.configure("Accent.TButton", font=("맑은 고딕", 11, "bold"), padding=(14, 11), background="#237a57", foreground="white")
        style.map("Accent.TButton", background=[("active", "#195f43"), ("disabled", "#aab9b3")])
        style.configure("TButton", font=("맑은 고딕", 10, "bold"), padding=(10, 8))
        style.configure("Horizontal.TProgressbar", troughcolor="#dbe5e1", background="#237a57", thickness=15)

    def build_ui(self) -> None:
        """설정, 실행 제어, 진행 로그 영역을 순서대로 구성한다."""
        container = ttk.Frame(self, padding=22)
        container.pack(fill="both", expand=True)
        ttk.Label(container, text="2way 차량 추적", style="Title.TLabel").pack(anchor="w")
        ttk.Label(container, text="동두천시 보건소 24시간 영상 · 유입/유출 자동 집계", style="Subtitle.TLabel").pack(anchor="w", pady=(2, 16))

        setup = ttk.Frame(container, style="Card.TFrame", padding=17)
        setup.pack(fill="x")
        ttk.Label(setup, text="분석 설정", style="Section.TLabel").grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))
        setup.columnconfigure(1, weight=1)
        self.add_path_row(setup, 1, "영상 폴더", self.source_var, self.choose_source)
        self.add_path_row(setup, 2, "결과 폴더", self.output_var, self.choose_output)
        ttk.Label(setup, text="모델", style="Card.TLabel").grid(row=3, column=0, sticky="w", padx=(0, 10), pady=5)
        ttk.Combobox(setup, textvariable=self.model_var, values=["yolo26n.pt", "yolo26s.pt", "yolo11n.pt", "yolo11s.pt"], width=20).grid(row=3, column=1, sticky="w", pady=5)
        ttk.Label(setup, text="GPU 장치", style="Card.TLabel").grid(row=3, column=1, sticky="e", padx=(0, 155), pady=5)
        ttk.Combobox(setup, textvariable=self.device_var, values=["0", "cpu"], width=9, state="readonly").grid(row=3, column=1, sticky="e", padx=(0, 70), pady=5)
        ttk.Label(setup, text="탐지 신뢰도", style="Card.TLabel").grid(row=3, column=1, sticky="e", padx=(0, 0), pady=5)
        ttk.Entry(setup, textvariable=self.conf_var, width=7).grid(row=3, column=2, sticky="e", pady=5)

        options = ttk.Frame(setup, style="Card.TFrame")
        options.grid(row=4, column=0, columnspan=3, sticky="w", pady=(10, 0))
        ttk.Checkbutton(
            options,
            text="출입 시각·차종 상세 CSV 저장",
            variable=self.detailed_records_var,
        ).pack(side="left", padx=(0, 18))
        ttk.Checkbutton(options, text="통과 순간 이미지 저장", variable=self.event_images_var).pack(side="left", padx=(0, 18))
        ttk.Checkbutton(options, text="검수용 전체 영상 저장 (용량 큼)", variable=self.review_video_var).pack(side="left")

        controls = ttk.Frame(container, style="Card.TFrame", padding=17)
        controls.pack(fill="x", pady=12)
        ttk.Label(controls, text="실행 순서", style="Section.TLabel").grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 9))
        ttk.Button(controls, text="1. 영역·통과선 설정", command=self.open_calibration).grid(row=1, column=0, padx=(0, 7), sticky="ew")
        ttk.Button(controls, text="유입/유출 방향 바꾸기", command=self.swap_direction).grid(row=1, column=1, padx=7, sticky="ew")
        ttk.Button(controls, text="2. 5분 시험 분석", command=self.run_test).grid(row=1, column=2, padx=7, sticky="ew")
        ttk.Button(controls, text="3. 전체 24시간 분석", style="Accent.TButton", command=self.run_full).grid(row=1, column=3, padx=(7, 0), sticky="ew")
        for column in range(4):
            controls.columnconfigure(column, weight=1)
        ttk.Label(controls, textvariable=self.direction_var, style="Card.TLabel").grid(row=2, column=0, columnspan=4, sticky="w", pady=(10, 0))

        progress_card = ttk.Frame(container, style="Card.TFrame", padding=17)
        progress_card.pack(fill="both", expand=True)
        header = ttk.Frame(progress_card, style="Card.TFrame")
        header.pack(fill="x")
        ttk.Label(header, text="진행 상황", style="Section.TLabel").pack(side="left")
        ttk.Button(header, text="분석 중지", command=self.stop_analysis).pack(side="right")
        ttk.Button(header, text="결과 폴더 열기", command=self.open_results).pack(side="right", padx=(0, 7))
        ttk.Progressbar(progress_card, variable=self.progress_var, maximum=100).pack(fill="x", pady=(11, 7))
        ttk.Label(progress_card, textvariable=self.status_var, style="Card.TLabel").pack(anchor="w", pady=(0, 8))
        self.log_text = tk.Text(progress_card, height=12, bg="#15231f", fg="#d9eee5", insertbackground="white", relief="flat", font=("Consolas", 9), padx=10, pady=8)
        self.log_text.pack(fill="both", expand=True)
        self.log("준비 완료")

    def add_path_row(self, parent, row, label, variable, command) -> None:
        """폴더 경로 입력창과 찾아보기 버튼 한 행을 추가한다."""
        ttk.Label(parent, text=label, style="Card.TLabel").grid(row=row, column=0, sticky="w", padx=(0, 10), pady=5)
        ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=5)
        ttk.Button(parent, text="찾기", command=command).grid(row=row, column=2, padx=(8, 0), pady=5)

    def choose_source(self) -> None:
        """입력 영상 폴더를 사용자가 선택하도록 한다."""
        selected = filedialog.askdirectory(initialdir=self.source_var.get())
        if selected:
            self.source_var.set(selected)

    def choose_output(self) -> None:
        """분석 결과를 저장할 폴더를 사용자가 선택하도록 한다."""
        selected = filedialog.askdirectory(initialdir=str(APP_DIR))
        if selected:
            self.output_var.set(selected)

    def save_form(self) -> dict:
        """화면의 최신 입력값을 검증해 config.json에 저장한다."""
        config = load_config(CONFIG_PATH)
        config["source_dir"] = self.source_var.get().strip()
        config["output_dir"] = self.output_var.get().strip() or "results"
        config["model"] = self.model_var.get().strip() or "yolo26s.pt"
        config["device"] = self.device_var.get()
        config["confidence"] = float(self.conf_var.get())
        config["save_detailed_records"] = self.detailed_records_var.get()
        config["save_event_images"] = self.event_images_var.get()
        config["save_review_video"] = self.review_video_var.get()
        save_config(CONFIG_PATH, config)
        self.config_data = config
        return config

    def open_calibration(self) -> None:
        """분석이 멈춘 상태에서 ROI·통과선 보정 창을 연다."""
        if self.worker and self.worker.is_alive():
            messagebox.showwarning("분석 중", "분석을 중지한 뒤 영역을 설정하세요.")
            return
        try:
            self.save_form()
            self.log("영역 설정 창을 열었습니다. 안내에 따라 클릭하세요.")
            result = calibrate(CONFIG_PATH)
            if result:
                self.log(f"영역 설정 저장: {result.name}")
                messagebox.showinfo("저장 완료", "관심영역과 통과선을 저장했습니다.\n이제 5분 시험 분석을 실행하세요.")
            else:
                self.log("영역 설정 취소")
        except Exception as error:
            messagebox.showerror("영역 설정 오류", str(error))
            self.log(f"오류: {error}")

    def swap_direction(self) -> None:
        """왼쪽→오른쪽 이동에 할당된 유입·유출 방향을 뒤집는다."""
        config = self.save_form()
        config["increasing_x_direction"] = "유출" if config.get("increasing_x_direction", "유출") == "유입" else "유입"
        save_config(CONFIG_PATH, config)
        self.config_data = config
        self.refresh_direction_text()
        self.log("유입/유출 방향 판정을 서로 바꿨습니다.")

    def refresh_direction_text(self) -> None:
        """현재 방향 설정을 사람이 읽을 수 있는 문장으로 갱신한다."""
        direction = self.config_data.get("increasing_x_direction", "유출")
        other = "유출" if direction == "유입" else "유입"
        self.direction_var.set(f"방향 기준: 화면 왼쪽→오른쪽 = {direction} / 오른쪽→왼쪽 = {other}")

    def run_test(self) -> None:
        """대표 주간 영상의 첫 5분만 검수 영상과 함께 분석한다."""
        try:
            config = self.save_form()
            day_video = find_day_video(Path(config["source_dir"]))
            test_config = copy.deepcopy(config)
            test_config["output_dir"] = "test_results"
            test_config["save_review_video"] = True
            self.start_worker(test_config, limit_seconds=300, video_paths=[day_video], title="5분 시험 분석")
        except Exception as error:
            messagebox.showerror("설정 오류", str(error))

    def run_full(self) -> None:
        """확인을 받은 뒤 설정된 전체 영상의 장시간 분석을 시작한다."""
        try:
            config = self.save_form()
            if not messagebox.askyesno("전체 분석", "8개 영상을 순서대로 분석합니다.\n노트북 전원을 연결하고 절전 모드를 꺼 주세요.\n\n시작할까요?"):
                return
            self.start_worker(config, title="24시간 전체 분석")
        except Exception as error:
            messagebox.showerror("설정 오류", str(error))

    def start_worker(self, config: dict, limit_seconds=None, video_paths=None, title="분석") -> None:
        """UI가 멈추지 않도록 BatchAnalyzer를 별도 스레드에서 실행한다."""
        if self.worker and self.worker.is_alive():
            messagebox.showwarning("분석 중", "이미 분석이 진행 중입니다.")
            return
        self.stop_event.clear()
        self.progress_var.set(0)
        self.status_var.set(f"{title} 준비 중…")
        self.log(f"{title} 시작")

        def work() -> None:
            # Tkinter 위젯은 메인 스레드에서만 안전하므로 모든 UI 갱신은 after로 전달한다.
            try:
                analyzer = BatchAnalyzer(
                    config,
                    APP_DIR,
                    log=lambda message: self.after(0, self.log, message),
                    progress=lambda value: self.after(0, self.set_progress, value, title),
                    stop_event=self.stop_event,
                )
                summary, events = analyzer.run(limit_seconds=limit_seconds, video_paths=video_paths)
                if self.stop_event.is_set():
                    self.after(0, self.status_var.set, "사용자가 분석을 중지했습니다.")
                else:
                    self.after(0, self.analysis_done, summary, events)
            except Exception as error:
                details = traceback.format_exc()
                self.after(0, self.log, details)
                self.after(0, self.status_var.set, f"오류: {error}")
                self.after(0, messagebox.showerror, "분석 오류", str(error))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def set_progress(self, value: float, title: str) -> None:
        """0~1 분석 진행률을 화면의 0~100% 값으로 변환한다."""
        percent = max(0.0, min(100.0, value * 100))
        self.progress_var.set(percent)
        self.status_var.set(f"{title} 진행 중 · {percent:.1f}%")

    def analysis_done(self, summary: Path, events: Path | None) -> None:
        """완료된 결과 경로를 로그와 알림창에 표시한다."""
        self.progress_var.set(100)
        self.status_var.set("분석이 완료되었습니다.")
        self.log(f"집계표: {summary}")
        if events is not None:
            self.log(f"통과기록: {events}")
        else:
            self.log("차량별 상세 기록은 저장하지 않았습니다.")
        messagebox.showinfo("분석 완료", f"결과를 저장했습니다.\n\n{summary.parent}")

    def stop_analysis(self) -> None:
        """현재 프레임 처리가 끝난 뒤 멈추도록 협력적 중지를 요청한다."""
        if self.worker and self.worker.is_alive():
            self.stop_event.set()
            self.status_var.set("현재 프레임 처리 후 중지합니다…")
            self.log("중지 요청")

    def open_results(self) -> None:
        """결과 폴더를 생성하고 Windows 탐색기로 연다."""
        config = self.save_form()
        path = Path(config["output_dir"])
        if not path.is_absolute():
            path = APP_DIR / path
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(path)

    def log(self, message: str) -> None:
        """로그 창 끝에 메시지를 추가하고 최신 줄로 스크롤한다."""
        self.log_text.insert("end", message.rstrip() + "\n")
        self.log_text.see("end")


if __name__ == "__main__":
    CounterApp().mainloop()
