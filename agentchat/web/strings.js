// This product's own interface strings, in AgentGUI's `{key: {en, zh}}` shape.
//
// Registered into AgentGUI's i18n module rather than kept in a second store, so
// the chat, the console and the quota meters all follow one language choice and
// one toggle. Keys are prefixed `ac.` so an upstream string can never be
// shadowed by one of ours.
//
// The sign-in page deliberately does not use this file: it carries its own six
// strings inline, because a sign-in form that depends on another module loading
// is a lockout waiting to happen.

import { register } from "/js/i18n.js";

register({
  // --- topbar and quota panel -----------------------------------------------
  "ac.sign_out": { en: "Sign out", zh: "退出登录" },
  "ac.admin": { en: "Admin", zh: "管理" },
  "ac.quota": { en: "Usage quota", zh: "用量额度" },
  "ac.quota_none": { en: "No quota limit", zh: "无额度限制" },
  "ac.quota_spent": { en: "{window} spent", zh: "{window}已用尽" },
  "ac.quota_left": { en: "{amount} left", zh: "剩余 {amount}" },
  "ac.quota_foot": {
    en: "Both limits roll: spend leaves a window as it ages out, so a refusal lifts by itself. A turn already running is never cut off.",
    zh: "两个额度都是滚动窗口：支出会随时间移出窗口，因此限制会自动解除。已经开始的一轮回复不会被中断。",
  },
  "ac.quota_exhausted": { en: "Quota exhausted.", zh: "额度已用尽。" },
  "ac.quota_retry": { en: " Try again after {when}.", zh: " 请在 {when} 之后重试。" },
  "ac.billing_error": {
    en: "Usage was not recorded: {error}",
    zh: "用量未能记录：{error}",
  },
  "ac.workspace_name": { en: "Workspace name", zh: "工作区名称" },
  "ac.workspace_default": { en: "default", zh: "default" },
  "ac.name_rule": {
    en: "Letters, digits, dot, dash and underscore",
    zh: "仅限字母、数字、点、短横线与下划线",
  },

  // --- meters ---------------------------------------------------------------
  "ac.window_budget": { en: "Weekly", zh: "每周" },
  "ac.window_burst": { en: "Burst", zh: "短时" },
  "ac.span_days": { en: "{days} days", zh: "{days} 天" },
  "ac.span_hours": { en: "{hours}h", zh: "{hours} 小时" },
  "ac.no_limit": { en: "{spent} · no limit", zh: "{spent} · 无限制" },
  "ac.of_limit": { en: "{spent} of {limit}", zh: "{spent} / {limit}" },
  "ac.exhausted": { en: "Exhausted", zh: "已用尽" },
  "ac.exhausted_until": {
    en: "Exhausted · frees up {when}",
    zh: "已用尽 · {when}恢复",
  },
  "ac.remaining": { en: "{amount} left", zh: "剩余 {amount}" },
  "ac.remaining_until": {
    en: "{amount} left · oldest spend ages out {when}",
    zh: "剩余 {amount} · 最早的支出 {when} 移出窗口",
  },
  "ac.until_now": { en: "any moment", zh: "随时" },
  "ac.until_minutes": { en: "in {n} min", zh: "{n} 分钟后" },
  "ac.until_hours": { en: "in {n}h", zh: "{n} 小时后" },
  "ac.until_days": { en: "in {n} days", zh: "{n} 天后" },

  // --- admin console --------------------------------------------------------
  "ac.admin_title": { en: "Agent Chat admin", zh: "Agent Chat 管理台" },
  "ac.back_to_chat": { en: "Back to chat", zh: "返回对话" },
  "ac.people": { en: "People", zh: "成员" },
  "ac.people_intro": {
    en: "Accounts are created here; there is no sign-up. Each row shows the two rolling windows this member is measured against.",
    zh: "账号只能在这里创建，没有自助注册。每一行显示该成员适用的两个滚动窗口。",
  },
  "ac.col_user": { en: "User", zh: "用户" },
  "ac.col_role": { en: "Role", zh: "角色" },
  "ac.col_quota": { en: "Quota", zh: "额度" },
  "ac.col_actions": { en: "Actions", zh: "操作" },
  "ac.col_when": { en: "When", zh: "时间" },
  "ac.col_model": { en: "Model", zh: "模型" },
  "ac.col_in": { en: "In", zh: "输入" },
  "ac.col_out": { en: "Out", zh: "输出" },
  "ac.col_cost": { en: "Cost", zh: "费用" },
  "ac.add_user": { en: "Add a user", zh: "添加用户" },
  "ac.username": { en: "Username", zh: "用户名" },
  "ac.password": { en: "Password", zh: "密码" },
  "ac.role": { en: "Role", zh: "角色" },
  // The option's value stays the API's own `member`/`admin`; only the label here
  // is localised, so a translation can never change what is submitted.
  "ac.role_member": { en: "member", zh: "成员" },
  "ac.role_admin": { en: "admin", zh: "管理员" },
  "ac.page_title": { en: "Admin · Agent Chat", zh: "管理 · Agent Chat" },
  "ac.create": { en: "Create", zh: "创建" },
  "ac.created": { en: "User created.", zh: "用户已创建。" },
  "ac.username_rule": {
    en: "Letters, digits, dot, dash and underscore",
    zh: "仅限字母、数字、点、短横线与下划线",
  },
  "ac.charges": { en: "Recent charges", zh: "近期计费" },
  // Split around the inline tag and the <code> path it names, so the sentence
  // reads naturally in both scripts instead of being assembled by concatenation.
  "ac.charges_intro_1": {
    en: "Every charge is priced from token counts by this server. A",
    zh: "每一笔费用都由本服务器按 token 数计价。标记",
  },
  "ac.charges_intro_2": {
    en: "row means the model was not listed in",
    zh: "的行表示该模型未列在",
  },
  "ac.charges_intro_3": { en: ".", zh: "中。" },
  "ac.everyone": { en: "everyone", zh: "全部用户" },
  "ac.disabled": { en: "disabled", zh: "已停用" },
  "ac.no_limits": { en: "no limits", zh: "无限制" },
  "ac.fallback": { en: "fallback", zh: "兜底价" },
  "ac.fallback_title": {
    en: "Billed at the fallback rate: this model is not listed in prices.toml",
    zh: "按兜底价计费：该模型未列入 prices.toml",
  },
  "ac.no_charges": { en: "No charges recorded yet.", zh: "还没有计费记录。" },
  "ac.action_quota": { en: "Quota…", zh: "额度…" },
  "ac.action_credit": { en: "Credit…", zh: "充值…" },
  "ac.action_password": { en: "Password…", zh: "密码…" },
  "ac.action_disable": { en: "Disable", zh: "停用" },
  "ac.action_enable": { en: "Enable", zh: "启用" },
  "ac.credit_prompt": {
    en: "Credit {user} how many USD?",
    zh: "为 {user} 充值多少美元？",
  },
  "ac.credited": { en: "Credited {amount} to {user}.", zh: "已为 {user} 充值 {amount}。" },
  "ac.password_prompt": {
    en: "New password for {user} (min 8 characters)",
    zh: "为 {user} 设置新密码（至少 8 位）",
  },
  "ac.password_set": {
    en: "Password set for {user}; they must sign in again.",
    zh: "已为 {user} 设置新密码，该账号需重新登录。",
  },
  "ac.quota_for": { en: "Quota for", zh: "额度设置：" },
  "ac.quota_intro": {
    en: "Leave an amount blank for no limit on that window.",
    zh: "金额留空表示该窗口不设限制。",
  },
  "ac.budget_usd": { en: "Weekly budget (USD)", zh: "周预算（美元）" },
  "ac.burst_usd": { en: "Burst limit (USD)", zh: "短时上限（美元）" },
  "ac.window_hours": { en: "Window (hours)", zh: "窗口长度（小时）" },
  "ac.unlimited": { en: "unlimited", zh: "无限制" },
  "ac.save": { en: "Save", zh: "保存" },
  "ac.cancel": { en: "Cancel", zh: "取消" },
  "ac.quota_saved": { en: "Quota saved for {user}.", zh: "已保存 {user} 的额度。" },
  "ac.signed_out": { en: "signed out", zh: "已退出登录" },
  "ac.whoami": { en: "{user} (admin)", zh: "{user}（管理员）" },
});
