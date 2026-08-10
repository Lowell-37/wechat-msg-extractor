(function () {
    "use strict";

    var progressStream = null;

    function focusStepHeading() {
        var workspace = document.getElementById("wizard-workspace");
        var heading = workspace && workspace.querySelector(".step-heading h1");
        heading = heading || (workspace && workspace.querySelector("h1, h2"));
        if (heading) {
            heading.setAttribute("tabindex", "-1");
            heading.focus({ preventScroll: true });
        } else if (workspace) {
            workspace.focus({ preventScroll: true });
        }
    }

    document.body.addEventListener("htmx:afterSwap", function (event) {
        if (event.detail.target.id === "wizard-workspace") {
            focusStepHeading();
        }
        event.detail.target.querySelectorAll("[data-export-form]").forEach(
            updatePrivacyRequirement
        );
        initializeExportProgress(event.detail.target);
    });

    document.body.addEventListener("htmx:beforeRequest", function (event) {
        var form = event.detail.elt.closest("[data-export-form], [data-export-retry]");
        if (!form) {
            return;
        }
        var button = form.querySelector('button[type="submit"]');
        if (button) {
            button.disabled = true;
            button.textContent = "正在启动导出…";
        }
    });

    document.addEventListener("change", function (event) {
        if (event.target.matches('[name="enable_ai"], [name="enable_voice"]')) {
            updatePrivacyRequirement(event.target.closest("form"));
        }
        var control = event.target.closest(".selection-control");
        if (!control) {
            return;
        }
        var form = control.closest("#selection-form");
        if (!form) {
            return;
        }
        var radio = control.closest('input[type="radio"][name="group_choice"]');
        if (radio) {
            form.querySelector('input[name="group_id"]').value = radio.value;
            form.querySelector('input[name="group_name"]').value = radio.dataset.groupName;
            form.querySelectorAll("[data-chatroom-row]").forEach(function (row) {
                var rowRadio = row.querySelector('input[name="group_choice"]');
                var rowSheet = row.querySelector('select[name="sheet_choice"]');
                var selected = rowRadio === radio;
                row.classList.toggle("chatroom-row--selected", selected);
                rowSheet.disabled = !selected;
                if (selected) {
                    form.querySelector('input[name="sheet_name"]').value = rowSheet.value;
                }
            });
            return;
        }
        if (control.matches('select[name="sheet_choice"]')) {
            form.querySelector('input[name="sheet_name"]').value = control.value;
        }
    }, true);

    function updatePrivacyRequirement(form) {
        if (!form) {
            return;
        }
        var consent = form.querySelector('[name="privacy_acknowledged"]');
        if (!consent) {
            return;
        }
        var externalEnabled = Array.from(
            form.querySelectorAll('[name="enable_ai"], [name="enable_voice"]')
        ).some(function (control) {
            return control.checked;
        });
        consent.required = externalEnabled;
        consent.setAttribute("aria-required", externalEnabled ? "true" : "false");
    }

    function initializeExportProgress(root) {
        var progress = root.querySelector && root.querySelector("[data-export-state]");
        if (!progress) {
            return;
        }
        if (progress.dataset.exportState === "failed") {
            restoreExportForm();
            return;
        }
        if (progress.dataset.exportState !== "active") {
            return;
        }
        if (progressStream) {
            progressStream.close();
        }
        progressStream = new EventSource(progress.dataset.progressUrl);
        progressStream.onmessage = function (event) {
            var payload = JSON.parse(event.data);
            updateProgress(progress, payload);
        };
        progressStream.onerror = function () {
            if (progress.dataset.exportState === "active") {
                showExportFailure(progress, "进度连接中断，请重试导出。", 0);
            }
            progressStream.close();
            progressStream = null;
        };
    }

    function updateProgress(progress, payload) {
        var percent = Math.max(0, Math.min(100, Number(payload.progress) || 0));
        var progressbar = progress.querySelector('[role="progressbar"]');
        progressbar.setAttribute("aria-valuenow", String(percent));
        progress.querySelector("[data-progress-fill]").style.width = percent + "%";
        progress.querySelector("[data-progress-percent]").textContent = percent + "%";
        progress.querySelector("[data-progress-message]").textContent = payload.message || "";
        progress.querySelector("[data-progress-phase]").textContent = phaseLabel(payload.stage);

        if (payload.stage === "done") {
            progress.dataset.exportState = "complete";
            progress.className = "export-progress export-progress--complete";
            var complete = progress.querySelector("[data-export-complete]");
            complete.hidden = false;
            complete.querySelector("[data-output-path]").textContent =
                payload.detail && payload.detail.path ? payload.detail.path : "";
            progressStream.close();
            progressStream = null;
        } else if (payload.stage === "error") {
            showExportFailure(progress, payload.message || "导出失败，请重试。", percent);
            progressStream.close();
            progressStream = null;
        }
    }

    function phaseLabel(stage) {
        var labels = {
            voice: "语音转写",
            warning: "降级处理",
            write: "写入任务",
            ai: "AI 分析",
            save: "保存文件",
            done: "导出完成",
            error: "导出未完成"
        };
        return labels[stage] || "导出进行中";
    }

    function showExportFailure(progress, message, percent) {
        progress.dataset.exportState = "failed";
        progress.className = "export-progress export-progress--failed";
        progress.querySelector('[role="progressbar"]').setAttribute(
            "aria-valuenow", String(percent)
        );
        progress.querySelector("[data-progress-message]").textContent = message;
        progress.querySelector("[data-progress-phase]").textContent = "导出未完成";
        progress.querySelector("[data-export-retry]").hidden = false;
        restoreExportForm();
    }

    function restoreExportForm() {
        var form = document.querySelector("[data-export-form]");
        if (form) {
            form.querySelectorAll("input, button").forEach(function (control) {
                control.disabled = false;
            });
            form.querySelector("[data-export-submit]").textContent = "重试导出";
        }
    }

    window.addEventListener("popstate", async function () {
        if (!/^\/wizard\/[1-3]$/.test(window.location.pathname)) {
            return;
        }
        var response = await fetch(window.location.pathname + "/partial", {
            headers: { "HX-Request": "true" }
        });
        if (!response.ok) {
            window.location.reload();
            return;
        }
        var html = await response.text();
        window.htmx.swap("#wizard-workspace", html, { swapStyle: "outerHTML" });
        window.requestAnimationFrame(focusStepHeading);
    });

    document.querySelectorAll("[data-export-form]").forEach(updatePrivacyRequirement);
    initializeExportProgress(document);
})();
