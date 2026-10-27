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
    const health = node("section", null, "card"); health.id = "backup-health";
    root.append(intro, notice, health);
    const tableNames = { sessions: "作答会话", events: "学习与帮助活动", works: "作品",
      work_revisions: "作品修订", work_requests: "保存重试记录", exposures: "题目与材料接触记录" };
    const size = bytes => `${(bytes / (1024 * 1024)).toFixed(2)} MiB`;
    function renderHealth(next) {
      const h = next.backup_health || {}, capacity = h.capacity || {};
      health.replaceChildren(node("h3", "当前记录与备份是否一致"));
      const counts = capacity.counts || {};
      health.append(node("p", `当前保存 ${counts.works || 0} 份作品、${counts.work_revisions || 0} 次作品修订、${counts.sessions || 0} 个作答会话。`));
      const generated = h.generated;
      if (generated) {
        const changed = generated.changed_tables.map(key => tableNames[key] || key).join("、");
        health.append(node("p", `最近生成备份：${ui.formatTime(generated.backup_created_at)}。${generated.freshness === "matches_current_assets"
          ? "生成时包含的记录与当前已保存记录一致。" : `生成后${changed || "学习记录"}已有变化，请生成并核验新备份。`}`));
        if (!generated.matches_current_code) health.append(node("p", "这次生成使用的是旧恢复码。旧文件仍需旧码；请用当前恢复码生成一份新备份。"));
      } else if (h.legacy_generation_at) {
        health.append(node("p", `曾于 ${ui.formatTime(h.legacy_generation_at)} 生成备份，但旧版本没有记录完整对比信息。请重新生成，并选取实际文件核验。`));
      } else health.append(node("p", "当前服务尚未为此档案生成带完整对比信息的备份。"));
      const verified = h.verified_file;
      if (verified) {
        health.append(node("p", `最近选取文件核验：${ui.formatTime(verified.recorded_at)}；文件包含截至 ${ui.formatTime(verified.backup_created_at)} 的记录。${verified.freshness === "matches_current_assets"
          ? "这份文件的学习记录与当前已保存记录一致。" : "当前记录与这份文件已有差异，请生成并核验新备份。"}`));
        if (!verified.matches_current_code) health.append(node("p", "核验的文件使用旧恢复码，需继续保留对应旧码，不能用新码替代。"));
      } else health.append(node("p", "尚未核验实际保存的文件。生成成功不证明文件已落盘：请从下载目录选取文件，再填写制作它时的恢复码核验。"));
      if (h.restored_snapshot) health.append(node("p", `最近导入的备份时点：${ui.formatTime(h.restored_snapshot.backup_created_at)}。${h.restored_snapshot.freshness === "matches_current_assets"
        ? "导入记录与当前已保存记录一致。" : "导入后已有记录变化，需更新备份。"}`, "muted"));
      if (capacity.payload_limit_bytes) {
        health.append(node("p", `完整记录预计 ${size(capacity.payload_bytes)}，加密文件预计 ${size(capacity.estimated_envelope_bytes)}。`));
        if (capacity.status === "near_limit") health.append(node("p", "记录量已达到当前备份上限的八成。请尽快下载并核验；继续积累较多作品前，按项目恢复说明准备完整学习数据库快照。系统不会删去旧记录以缩小备份。"));
        if (capacity.status === "exceeded") health.append(node("p", "当前记录已超出单份便携备份上限，生成将明确失败。原记录仍保留；请按项目恢复说明保存完整学习数据库快照并迁移，不要清理原学习库或误把部分导出当完整备份。"));
        const details = node("details"); details.append(node("summary", "查看容量与兼容范围"),
          node("p", `当前上限：内容 ${size(capacity.payload_limit_bytes)}，文件 ${size(capacity.envelope_limit_bytes)}；每类记录最多 ${capacity.row_limit_per_table.toLocaleString()} 条。`));
        const list = node("ul"); Object.entries(counts).forEach(([key,value]) => list.append(node("li", `${tableNames[key] || key}：${value}`)));
        const c = h.compatibility;
        if (c) details.append(node("p", `支持加密备份版本 ${c.backup_versions.join("、")}，内容版本 ${c.content_schemas.join("、")}，学习记录版本 ${c.learning_schemas.join("、")}。${c.unsupported_action}`));
        details.append(list); health.append(details);
      }
      health.append(node("p", "文件核验确认所选文件在此刻可解密、记录结构完整，并比较保存时点；它不替代在另一安装中的实际恢复，也不评价学习效果。", "muted"));
      const refresh = button("重新检查备份状态", () => run(refresh, async () => {
        updateSwitchProtection(await api("/api/profile")); notice.textContent = "已根据当前保存的完整记录更新备份状态。";
      })); health.append(refresh);
    }
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
      updateSwitchProtection(await api("/api/profile"));
      notice.textContent = "加密备份已生成，并已请求浏览器保存。请确认下载位置，再选取实际文件核验；同时保留制作它时的恢复码。";
    }), "primary"); backupButton.disabled = !status.enabled; backup.append(backupButton); root.append(backup);
    const verifySection = node("section", null, "card"), verifyFile = node("input"), verifyCode = node("input");
    const verifyFileLabel = node("label", "从下载目录选择已保存的备份"), verifyCodeLabel = node("label", "制作这份备份时的恢复码");
    verifyFile.id = "verify-backup-file"; verifyFile.type = "file"; verifyFile.accept = ".json,application/json"; verifyFileLabel.htmlFor = verifyFile.id;
    verifyCode.id = "verify-backup-code"; verifyCode.type = "password"; verifyCode.autocomplete = "off"; verifyCodeLabel.htmlFor = verifyCode.id;
    verifySection.append(node("h3", "核验已保存的文件"), node("p", "选择实际文件并填写对应恢复码。核验不会导入记录或切换档案；旧备份需要旧恢复码。"),
      verifyFileLabel, verifyFile, verifyCodeLabel, verifyCode);
    async function selectedBackup(input) {
      const selected = input.files[0];
      if (!selected) throw new Error("请先选择完整备份文件。");
      const maximum = status.backup_health?.capacity?.envelope_limit_bytes || 70 * 1024 * 1024;
      if (selected.size > maximum) throw new Error(`备份超过当前版本支持的 ${size(maximum)} 文件上限。请保留原文件和对应恢复码，使用支持该容量的版本。`);
      // Preserve duplicate keys for the strict server parser instead of having
      // JSON.parse silently discard them before integrity validation.
      return await selected.text();
    }
    const verifyButton = button("核验文件，不导入", () => run(verifyButton, async () => {
      const result = await api("/api/profile/verify-backup", { envelope: await selectedBackup(verifyFile), recovery_code: verifyCode.value });
      updateSwitchProtection(await api("/api/profile"));
      const comparison = !result.matches_current_profile ? "这是另一档案的文件，当前档案保持原样。"
        : result.matches_current_assets ? "文件包含当前已保存的学习记录。" : "文件与当前记录已有差异，请生成并核验新备份。";
      notice.textContent = `文件核验通过：包含 ${result.counts.works} 份作品、${result.counts.work_revisions} 次修订、${result.counts.sessions} 个作答会话。${comparison}未导入记录或切换档案。请继续保存文件及对应恢复码。`;
    })); verifySection.append(verifyButton); root.append(verifySection);
    const recover = node("section", null, "card"), recoveryCode = node("input"), recoverLabel = node("label", "已有档案的恢复码");
    recoveryCode.id = "recover-code"; recoveryCode.type = "password"; recoveryCode.autocomplete = "off"; recoverLabel.htmlFor = recoveryCode.id;
    recover.append(node("h3", "找回或迁移已有档案"), node("p", "换浏览器或清理站点数据：输入恢复码找回同一学习服务上的档案。换电脑或重装学习服务：选择备份文件后导入。切换前，已有学习记录的当前档案需要保留可用的恢复方式。"));
    const switchNotice = node("p", "", "muted"), currentCode = node("input"), currentLabel = node("label", "切换保护：当前档案的恢复码");
    currentCode.id = "current-profile-code"; currentCode.type = "password"; currentCode.autocomplete = "off";
    currentLabel.htmlFor = currentCode.id;
    recover.append(switchNotice, currentLabel, currentCode, recoverLabel, recoveryCode);
    function updateSwitchProtection(next) {
      status = next;
      renderHealth(next);
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
      await api("/api/profile/restore", { envelope: await selectedBackup(file), recovery_code: recoveryCode.value,
        current_recovery_code: currentCode.value }); ui.identityChanged();
    })); recover.append(fileLabel, file, restore); root.append(recover);
  }
  return { mount };
})();
