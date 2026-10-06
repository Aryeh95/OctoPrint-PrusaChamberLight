$(function () {
    function ChamberLightViewModel(parameters) {
        var self = this;
        self.loginState = parameters[0];
        self.access = parameters[1];
        self.printerState = parameters[2];
        self.settings = parameters[3];

        self.lightOn = ko.observable(true);
        self.brightness = ko.observable(100);
        self.brightnessDraft = ko.observable(100);
        self.cookieSet = ko.observable(false);
        self.firmwareUuid = ko.observable(null);
        self.machineType = ko.observable(null);
        self.connectEnabled = ko.observable(false);
        self.cookieDraft = ko.observable("");
        self.busy = ko.observable(false);
        self.error = ko.observable("");
        self.syncResult = ko.observable("");

        self.tokenExpires = ko.observable(null);
        self.autoRenew = ko.observable(false);
        self.loginError = ko.observable(null);
        self.tokenStatus = ko.pureComputed(function () {
            if (!self.cookieSet()) return "not set";
            var exp = self.tokenExpires();
            if (self.autoRenew()) {
                return "renews automatically" + (exp ? " (current token valid until " + new Date(exp * 1000).toLocaleString() + ")" : "");
            }
            if (!exp) return "set (expiry unknown)";
            var when = new Date(exp * 1000).toLocaleString();
            return exp * 1000 < Date.now() ? "expired " + when : "set, expires " + when;
        });

        self.canControl = ko.pureComputed(function () {
            return self.loginState.hasPermission(self.access.permissions.CONTROL);
        });

        self.update = function (state) {
            self.lightOn(state.light_on);
            self.brightness(state.brightness);
            self.brightnessDraft(state.brightness);
            self.cookieSet(state.cookie_set);
            self.tokenExpires(state.token_expires);
            self.autoRenew(state.auto_renew);
            self.loginError(state.login_error);
            self.firmwareUuid(state.firmware_uuid);
            self.machineType(state.machine_type);
            self.connectEnabled(state.connect_enabled);
        };

        self.fetch = function () {
            OctoPrint.simpleApiGet("chamberlight").done(self.update);
        };

        self.command = function (command, data) {
            self.busy(true);
            self.error("");
            return OctoPrint.simpleApiCommand("chamberlight", command, data || {})
                .done(self.update)
                .fail(function (xhr) {
                    self.error(xhr.responseText || "Request failed");
                })
                .always(function () {
                    self.busy(false);
                });
        };

        self.toggle = function () {
            if (!self.printerState.isOperational()) return;
            self.command("toggle");
        };

        self.applyBrightness = function () {
            var value = parseInt(self.brightnessDraft(), 10);
            if (value === self.brightness()) return;
            self.command("brightness", { value: value }).fail(function () {
                self.brightnessDraft(self.brightness());
            });
        };

        self.saveCookie = function () {
            self.command("set_cookie", { cookie: self.cookieDraft() }).done(function () {
                self.cookieDraft("");
            });
        };

        self.sync = function () {
            self.syncResult("...");
            self.command("sync")
                .done(function (state) {
                    self.syncResult("OK, Connect reports " + state.brightness + "%");
                })
                .fail(function (xhr) {
                    self.syncResult(xhr.responseText || "Failed");
                });
        };

        self.onUserLoggedIn = self.fetch;
        self.onStartupComplete = function () {
            if (self.loginState.isUser()) self.fetch();
        };

        self.onDataUpdaterPluginMessage = function (plugin, data) {
            if (plugin === "chamberlight") self.update(data);
        };
    }

    OCTOPRINT_VIEWMODELS.push({
        construct: ChamberLightViewModel,
        dependencies: ["loginStateViewModel", "accessViewModel", "printerStateViewModel", "settingsViewModel"],
        elements: ["#navbar_plugin_chamberlight", "#sidebar_plugin_chamberlight", "#settings_plugin_chamberlight"]
    });
});
