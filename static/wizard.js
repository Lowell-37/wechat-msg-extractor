(function () {
    "use strict";

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
    });

    document.addEventListener("change", function (event) {
        var radio = event.target.closest('input[type="radio"][name="group_id"]');
        if (!radio) {
            return;
        }
        var form = radio.closest("#selection-form");
        if (!form) {
            return;
        }
        form.querySelector('input[name="group_name"]').value = radio.dataset.groupName;
        form.querySelectorAll("[data-chatroom-row]").forEach(function (row) {
            var rowRadio = row.querySelector('input[name="group_id"]');
            var rowSheet = row.querySelector('select[name="sheet_name"]');
            var selected = rowRadio === radio;
            row.classList.toggle("chatroom-row--selected", selected);
            rowSheet.disabled = !selected;
        });
    }, true);

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
})();
