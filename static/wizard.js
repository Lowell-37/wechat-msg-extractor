(function () {
    "use strict";

    document.body.addEventListener("htmx:afterSwap", function (event) {
        if (event.detail.target.id === "wizard-workspace") {
            event.detail.target.focus({ preventScroll: true });
        }
    });
})();
