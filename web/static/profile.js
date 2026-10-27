/* Explicit recovery setup and portable, encrypted learning-asset backups. */
window.CNKGProfile = (() => {
  function download(value, filename, type = "application/json;charset=utf-8") {
    const blob = new Blob([typeof value === "string" ? value : JSON.stringify(value, null, 2)], { type });
    const url = URL.createObjectURL(blob), link = document.createElement("a");
    link.href = url; link.download = filename; document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  async function mount(root, ui) {
    const { api, node, button } = ui;
    root.replaceChildren();
    let status = await api("/api/profile");
    const notice = node("p", "", "muted"); notice.setAttribute("role", "status");
    const intro = node("section", null, "card");
    intro.append(node("h3", status.enabled ? status.label : "为当前学习记录设置恢复方式"),
      node("p", status.enabled ? "已设置恢复码。同一学习服务上可凭恢复码找回档案；迁移或重装时，需要加密备份和对应恢复码。" : "当前记录已保存在本机学习服务中。先创建恢复码，再下载完整备份，才能在清理浏览器数据或更换环境后找回。"));
    if (status.last_backup_at) intro.append(node("p", `最近备份：${ui.formatTime(status.last_backup_at)}`, "muted"));
    root.append(intro, notice);
    let busy = false, freshCode = null;
    async function run(control, action) {
      if (busy || !await ui.prepare()) return;
      busy = true; ui.busy(true); control.disabled = true; notice.textContent = "正在处理…";
      try { await action(); } catch (error) {
        notice.textContent = error.message;
        // Another tab or the draft flush may have added assets since mounting.
        // Refresh the recovery route without losing either entered credential.
        try { updateSwitchProtection(await api("/api/profile")); } catch (_) { /* Keep the original error. */ }
      }
      finally { busy = false; ui.busy(false); control.disabled = false; }
    }
    function secretCard(code) {
      freshCode = code;
      for (const previous of root.querySelectorAll(".recovery-code")) previous.remove();
      const card = node("section", null, "card recovery-code");
      card.append(node("h3", "保存这次恢复码"), node("p", "恢复码只在创建时显示。请把它与备份分开保存；持有码的人可以恢复你的学习档案。更换恢复码后，旧备份仍需原来的码解开。"));
      const label = node("label", "当前恢复码"), input = node("textarea");
      input.id = "new-recovery-code"; input.value = code; input.readOnly = true; input.rows = 3; label.htmlFor = input.id;
      const copy = button("复制恢复码", async () => {
        try { await navigator.clipboard.writeText(code); notice.textContent = "恢复码已复制，请妥善保存。"; }
        catch (_) { input.select(); notice.textContent = "已选中恢复码，可手动复制。"; }
      });
      card.append(label, input, copy, button("下载恢复码文本", () => download(
        `K12 数学学习档案恢复码\n${code}\n\n请与加密备份分开保存。旧备份使用生成该备份时的恢复码。\n`,
        "cnkg-recovery-code.txt", "text/plain;charset=utf-8")));
      intro.after(card); backupCode.value = code;
    }
    if (!status.enabled) {
      const label = node("label", "档案名称"), input = node("input");
      input.id = "profile-label"; input.value = "我的学习档案"; input.maxLength = 80; label.htmlFor = input.id;
      const enable = button("创建恢复码", () => run(enable, async () => {
        const data = await api("/api/profile/enable", { label: input.value });
        secretCard(data.recovery_code); enable.remove(); input.disabled = true; backupButton.disabled = false;
        updateSwitchProtection(await api("/api/profile"));
        notice.textContent = "恢复方式已创建，原记录保持完整。现在保存恢复码并下载备份。";
      }), "primary");
      intro.append(label, input, enable);
    } else {
      const details = node("details"), summary = node("summary", "更换恢复码");
      details.append(summary, node("p", "旧恢复码将不能找回此服务上的档案，之前登录的浏览器也需重新恢复。已经下载的旧备份仍使用原来的码。"));
      const rotate = button("生成新恢复码", () => run(rotate, async () => {
        const data = await api("/api/profile/rotate", {}); secretCard(data.recovery_code);
        currentCode.value = ""; updateSwitchProtection(await api("/api/profile"));
        notice.textContent = "恢复码已更换。请保存新码并下载新的备份。";
      })); details.append(rotate); intro.append(details);
    }
    const backup = node("section", null, "card"), backupCode = node("input"), codeLabel = node("label", "当前恢复码");
    backupCode.id = "backup-code"; backupCode.type = "password"; backupCode.autocomplete = "off"; codeLabel.htmlFor = backupCode.id;
    backup.append(node("h3", "下载完整备份"), node("p", "备份包含作答、作品历次修订、未解决事项、提示记录和当时的题目版本。备份文件经过加密；可读学习记录导出不能用于恢复。"), codeLabel, backupCode);
    const backupButton = button("下载加密备份", () => run(backupButton, async () => {
      const envelope = await api("/api/profile/backup", { recovery_code: backupCode.value || freshCode });
      download(envelope, `cnkg-learning-backup-${new Date().toISOString().slice(0, 10)}.json`);
      notice.textContent = "加密备份已下载。请同时保留这份备份对应的恢复码；后续学习后再更新备份。";
    }), "primary"); backupButton.disabled = !status.enabled; backup.append(backupButton); root.append(backup);
    const recover = node("section", null, "card"), recoveryCode = node("input"), recoverLabel = node("label", "已有档案的恢复码");
    recoveryCode.id = "recover-code"; recoveryCode.type = "password"; recoveryCode.autocomplete = "off"; recoverLabel.htmlFor = recoveryCode.id;
    recover.append(node("h3", "找回或迁移已有档案"), node("p", "换浏览器或清理站点数据：输入恢复码找回同一学习服务上的档案。换电脑或重装学习服务：选择备份文件后导入。切换前，已有学习记录的当前档案需要保留可用的恢复方式。"));
    const switchNotice = node("p", "", "muted"), currentCode = node("input"), currentLabel = node("label", "切换保护：当前档案的恢复码");
    currentCode.id = "current-profile-code"; currentCode.type = "password"; currentCode.autocomplete = "off";
    currentLabel.htmlFor = currentCode.id;
    recover.append(switchNotice, currentLabel, currentCode, recoverLabel, recoveryCode);
    function updateSwitchProtection(next) {
      status = next;
      const requirement = status.switch_protection?.requirement || "none";
      currentLabel.hidden = currentCode.hidden = requirement !== "verify_current_code";
      switchNotice.textContent = requirement === "enable_recovery"
        ? "当前档案已有学习记录。请先在上方创建恢复码，把它另行保存，再回来切换。"
        : requirement === "verify_current_code"
          ? "请先保存当前档案的恢复码，再从保存的位置粘贴到切换保护栏。下方另填要找回的档案恢复码；需要返回时，用保存的当前码即可找回这些记录。"
          : "此浏览器当前没有学习记录，可直接找回或导入已有档案。";
    }
    updateSwitchProtection(status);
    const recoverButton = button("凭恢复码找回", () => run(recoverButton, async () => {
      await api("/api/profile/recover", { recovery_code: recoveryCode.value,
        current_recovery_code: currentCode.value }); ui.identityChanged();
    })); recover.append(recoverButton);
    const file = node("input"), fileLabel = node("label", "完整备份文件");
    file.type = "file"; file.accept = ".json,application/json"; file.id = "restore-backup"; fileLabel.htmlFor = file.id;
    const restore = button("导入备份并恢复", () => run(restore, async () => {
      const selected = file.files[0];
      if (!selected) throw new Error("请先选择完整备份文件。");
      if (selected.size > 70 * 1024 * 1024) throw new Error("备份超过当前版本支持的 70 MiB 文件上限。");
      let envelope; try { envelope = JSON.parse(await selected.text()); } catch (_) { throw new Error("文件不是有效的 JSON 备份。"); }
      await api("/api/profile/restore", { envelope, recovery_code: recoveryCode.value,
        current_recovery_code: currentCode.value }); ui.identityChanged();
    })); recover.append(fileLabel, file, restore); root.append(recover);
  }
  return { mount };
})();
