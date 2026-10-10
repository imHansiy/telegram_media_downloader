(() => {
// Keep the existing server login protocol; this redesign does not change authentication.
const encrypt = (word) => {
  const key = CryptoJS.enc.Utf8.parse("1234123412ABCDEF");
  const iv = CryptoJS.enc.Utf8.parse("ABCDEF1234123412");
  return CryptoJS.AES.encrypt(CryptoJS.enc.Utf8.parse(word), key, {
    iv,
    mode: CryptoJS.mode.CBC,
    padding: CryptoJS.pad.Pkcs7,
  }).ciphertext.toString(CryptoJS.enc.Base64);
};
const password = document.getElementById("password");
const toggle = document.getElementById("toggle-password");
const submit = document.getElementById("login-submit");
const error = document.getElementById("login-error");
toggle.addEventListener("click", () => {
  const show = password.type === "password";
  password.type = show ? "text" : "password";
  toggle.textContent = show ? "隐藏" : "显示";
  toggle.setAttribute("aria-label", show ? "隐藏密码" : "显示密码");
  toggle.setAttribute("aria-pressed", String(show));
});
document
  .getElementById("login-form")
  .addEventListener("submit", async (event) => {
    event.preventDefault();
    if (submit.disabled) return;
    error.hidden = true;
    submit.disabled = true;
    submit.textContent = "正在解锁…";
    try {
      const response = await fetch("/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password: encrypt(password.value) }),
      });
      if (!response.ok)
        throw new Error("暂时无法连接工作台，请稍后重试。");
      const result = await response.json();
      if (result.code !== "1")
        throw new Error("管理密码不正确，请重新输入。");
      let target = "/files";
      try {
        const next = new URL(
          new URLSearchParams(location.search).get("next") || target,
          location.origin,
        );
        if (next.origin === location.origin && next.pathname !== "/login")
          target = next.href;
      } catch {
        /* Invalid return locations safely fall back to the library. */
      }
      location.assign(target);
    } catch (reason) {
      error.textContent =
        reason instanceof Error ? reason.message : "解锁失败，请重试。";
      error.hidden = false;
      password.focus();
      password.select();
    } finally {
      submit.disabled = false;
      submit.textContent = "解锁工作台 →";
    }
  });

})();

