/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState } from 'react';
import {
  AlertCircle,
  ArrowRight,
  Bot,
  ChevronDown,
  ChevronUp,
  CheckCircle2,
  Globe2,
  Loader2,
  LogOut,
  LockKeyhole,
  Copy,
  Pencil,
  Plus,
  RefreshCw,
  Save,
  ShieldAlert,
  ShieldCheck,
  Smartphone,
  Trash2,
  UserCheck,
  UserPlus,
  Users,
} from 'lucide-react';
import { BotAccessConfig, BotAccessMode, TelegramAccount } from '../types';

interface AccountManagerProps {
  accounts: TelegramAccount[];
  activeAccountId: string | null;
  sessionExists: boolean;
  onSelectAccount: (id: string) => void;
  onCreateProfile: (name: string, copyCurrentConfig: boolean, activate?: boolean) => Promise<void> | void;
  onRenameProfile: (id: string, name: string) => Promise<void> | void;
  onDeleteProfile: (id: string) => Promise<void> | void;
  onDisconnectAccount: (id: string) => Promise<void> | void;
  onStartAccount: (id: string) => Promise<void> | void;
  onStopAccount: (id: string) => Promise<void> | void;
  onConnectSavedSession: (id?: string) => Promise<void>;
  onSendCode: (
    phoneNumber: string,
    apiId?: string,
    apiHash?: string,
    profileId?: string,
    createProfile?: boolean,
    profileName?: string,
  ) => Promise<void>;
  onVerifyCode: (code: string) => Promise<{ needsPassword: boolean }>;
  onVerifyPassword: (password: string) => Promise<void>;
  onRefresh: () => Promise<void>;
  botAccess: BotAccessConfig;
  onSaveBotAccess: (config: BotAccessConfig) => Promise<void> | void;
}

export function AccountManager({
  accounts,
  activeAccountId,
  sessionExists,
  onSelectAccount,
  onCreateProfile,
  onRenameProfile,
  onDeleteProfile,
  onDisconnectAccount,
  onStartAccount,
  onStopAccount,
  onConnectSavedSession,
  onSendCode,
  onVerifyCode,
  onVerifyPassword,
  onRefresh,
  botAccess,
  onSaveBotAccess,
}: AccountManagerProps) {
  const [step, setStep] = useState<'list' | 'phone' | 'code' | 'password'>('list');
  const [phoneNumber, setPhoneNumber] = useState('');
  const [apiId, setApiId] = useState('');
  const [apiHash, setApiHash] = useState('');
  const [useConfiguredApi, setUseConfiguredApi] = useState(true);
  const [code, setCode] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState('');
  const [settingsAccountId, setSettingsAccountId] = useState<string | null>(null);
  const [botAccessMode, setBotAccessMode] = useState<BotAccessMode>(botAccess.mode);
  const [allowedUsersText, setAllowedUsersText] = useState(botAccess.allowedUsers.join('\n'));
  const [accessFeedback, setAccessFeedback] = useState('');
  const [newProfileName, setNewProfileName] = useState('');
  const [renamingProfileId, setRenamingProfileId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [loginTargetProfileId, setLoginTargetProfileId] = useState<string | null>(null);

  useEffect(() => {
    setBotAccessMode(botAccess.mode);
    setAllowedUsersText(botAccess.allowedUsers.join('\n'));
  }, [botAccess]);

  const accessModes: Array<{
    mode: BotAccessMode;
    label: string;
    description: string;
    icon: React.ElementType;
  }> = [
    { mode: 'self', label: '仅自己', description: '只有当前登录账号可投递', icon: LockKeyhole },
    { mode: 'allowed', label: '指定用户', description: '列表内用户可私聊投递', icon: UserCheck },
    { mode: 'public', label: '全部用户', description: '任何人可私聊投递', icon: Globe2 },
  ];

  const selectedAccessLabel = accessModes.find((item) => item.mode === botAccess.mode)?.label || '仅自己';
  const loginTargetAccount = accounts.find((acct) => acct.id === loginTargetProfileId);

  const profileLabel = (acct: TelegramAccount) => acct.profileName || acct.firstName || 'Telegram Profile';

  const parseAllowedUsers = (value: string) => (
    value
      .split(/[\n,，;；]+/)
      .map((item) => item.trim())
      .filter(Boolean)
  );

  const run = async (fn: () => Promise<void>) => {
    setLoading(true);
    setErrorMessage('');
    try {
      await fn();
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setLoading(false);
    }
  };

  const handleSendCode = (event: React.FormEvent) => {
    event.preventDefault();
    run(async () => {
      const cleanPhoneNumber = phoneNumber.trim();
      const cleanApiId = apiId.trim();
      const cleanApiHash = apiHash.trim();
      await onSendCode(
        cleanPhoneNumber,
        useConfiguredApi ? undefined : cleanApiId,
        useConfiguredApi ? undefined : cleanApiHash,
        loginTargetProfileId || undefined,
        !loginTargetProfileId,
        loginTargetProfileId && loginTargetAccount ? profileLabel(loginTargetAccount) : cleanPhoneNumber,
      );
      setStep('code');
    });
  };

  const handleVerifyCode = (event: React.FormEvent) => {
    event.preventDefault();
    run(async () => {
      const result = await onVerifyCode(code);
      if (result.needsPassword) {
        setStep('password');
      } else {
        setStep('list');
        setCode('');
        setLoginTargetProfileId(null);
      }
    });
  };

  const handleVerifyPassword = (event: React.FormEvent) => {
    event.preventDefault();
    run(async () => {
      await onVerifyPassword(password);
      setStep('list');
      setCode('');
      setPassword('');
      setLoginTargetProfileId(null);
    });
  };

  const handleCreateProfile = (copyCurrentConfig: boolean) => {
    run(async () => {
      const fallbackName = `账户档案 ${accounts.length + 1}`;
      await onCreateProfile(newProfileName.trim() || fallbackName, copyCurrentConfig, false);
      setNewProfileName('');
    });
  };

  const startRename = (acct: TelegramAccount) => {
    setRenamingProfileId(acct.id);
    setRenameValue(profileLabel(acct));
  };

  const handleRename = (acct: TelegramAccount) => {
    run(async () => {
      const nextName = renameValue.trim();
      if (!nextName) return;
      await onRenameProfile(acct.id, nextName);
      setRenamingProfileId(null);
      setRenameValue('');
    });
  };

  const handleDeleteProfile = (acct: TelegramAccount) => {
    if (!window.confirm(`删除账号档案「${profileLabel(acct)}」？`)) return;
    run(async () => {
      await onDeleteProfile(acct.id);
    });
  };

  const startLogin = (profileId?: string, defaultPhone?: string) => {
    setErrorMessage('');
    setLoginTargetProfileId(profileId || null);
    setPhoneNumber(defaultPhone || '');
    setStep('phone');
  };

  const handleSaveBotAccess = () => {
    run(async () => {
      const nextAccess = {
        mode: botAccessMode,
        allowedUsers: parseAllowedUsers(allowedUsersText),
      } as BotAccessConfig;
      await onSaveBotAccess(nextAccess);
      setAccessFeedback('Bot 投递权限已保存。');
      setTimeout(() => setAccessFeedback(''), 2500);
    });
  };

  return (
    <div className="space-y-6">
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 flex flex-col sm:flex-row items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <div className="p-2.5 rounded-lg bg-indigo-500/10 text-indigo-400 border border-indigo-500/20">
            <Users className="w-5 h-5" />
          </div>
          <div>
            <h2 className="text-xs font-semibold text-slate-200">Telegram 账号与 Session 对话</h2>
            <p className="text-[10px] text-slate-500">
              使用真实 MTProto 登录流程，支持验证码和 Telegram 二步验证密码。
            </p>
          </div>
        </div>

        <div className="flex flex-wrap gap-2">
          <button
            onClick={() => run(onRefresh)}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 transition-all cursor-pointer"
          >
            <RefreshCw className="w-4 h-4" />
            刷新状态
          </button>
          {step === 'list' && (
            <button
              onClick={() => startLogin()}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white transition-all cursor-pointer"
            >
              <Plus className="w-4 h-4" />
              连接新电报会话
            </button>
          )}
        </div>
      </div>

      {errorMessage && (
        <div className="bg-rose-500/10 border border-rose-500/25 p-3 rounded-lg flex items-start gap-2 text-xs text-rose-400">
          <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
          <p className="font-medium leading-relaxed">{errorMessage}</p>
        </div>
      )}

      {step === 'list' && (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            handleCreateProfile(true);
          }}
          className="bg-slate-900 border border-slate-800 rounded-xl p-4 flex flex-col lg:flex-row gap-3 lg:items-center"
        >
          <div className="flex items-center gap-2.5 min-w-0 lg:w-56">
            <div className="p-2 rounded-lg bg-slate-950 text-indigo-400 border border-slate-805">
              <UserPlus className="w-4 h-4" />
            </div>
            <div className="min-w-0">
              <p className="text-xs text-slate-200 font-semibold">账号档案</p>
              <p className="text-[10px] text-slate-500">独立保存 session、配置和 Bot 权限</p>
            </div>
          </div>
          <input
            value={newProfileName}
            onChange={(event) => setNewProfileName(event.target.value)}
            placeholder="新档案名称"
            className="flex-1 min-w-0 bg-slate-950 border border-slate-805 focus:border-indigo-500 rounded-lg px-3 py-2 text-xs text-slate-200 focus:outline-none"
          />
          <div className="flex flex-wrap gap-2">
            <button
              type="submit"
              disabled={loading}
              className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 disabled:opacity-60 text-white text-xs font-semibold transition-all cursor-pointer"
            >
              <Copy className="w-3.5 h-3.5" />
              复制配置新建
            </button>
            <button
              type="button"
              disabled={loading}
              onClick={() => handleCreateProfile(false)}
              className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg bg-slate-800 hover:bg-slate-750 disabled:opacity-60 text-slate-200 text-xs font-semibold transition-all cursor-pointer"
            >
              <Plus className="w-3.5 h-3.5" />
              新建空档案
            </button>
          </div>
        </form>
      )}

      {step === 'list' && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {accounts.map((acct) => {
            const isActive = activeAccountId === acct.id;
            const hasSession = Boolean(acct.hasSession);
            const isRunning = Boolean(acct.isRunning || acct.status === 'connected');
            const displayName = profileLabel(acct);
            const identity = acct.username || acct.userId || (hasSession ? 'saved_session' : '未登录');
            return (
              <div
                id={`acct-card-${acct.id}`}
                key={acct.id}
                className={`bg-slate-900 border rounded-xl overflow-hidden shadow-md transition-all ${
                  isActive ? 'border-indigo-500 ring-1 ring-indigo-500/30' : 'border-slate-805/80 hover:border-slate-700/80'
                }`}
              >
                <div className="p-4 border-b border-slate-805 bg-slate-950/20 flex justify-between items-start">
                  <div className="flex items-center gap-2.5">
                    <div className="w-8 h-8 rounded-full bg-slate-800 flex items-center justify-center text-indigo-400 font-bold border border-slate-750 text-xs">
                      {displayName.substring(0, 2).toUpperCase()}
                    </div>
                    <div className="min-w-0">
                      {renamingProfileId === acct.id ? (
                        <div className="flex items-center gap-1.5">
                          <input
                            value={renameValue}
                            onChange={(event) => setRenameValue(event.target.value)}
                            className="w-32 bg-slate-950 border border-slate-700 focus:border-indigo-500 rounded px-2 py-1 text-[11px] text-slate-200 focus:outline-none"
                            autoFocus
                          />
                          <button
                            type="button"
                            onClick={() => handleRename(acct)}
                            className="px-2 py-1 rounded bg-indigo-600 hover:bg-indigo-500 text-white text-[10px] font-semibold cursor-pointer"
                          >
                            保存
                          </button>
                        </div>
                      ) : (
                        <div className="flex items-center gap-1.5 min-w-0">
                          <h3 className="text-xs font-semibold text-slate-200 truncate">{displayName}</h3>
                          <button
                            type="button"
                            onClick={() => startRename(acct)}
                            className="p-0.5 rounded text-slate-500 hover:text-indigo-300 hover:bg-slate-800 cursor-pointer"
                            title="重命名档案"
                          >
                            <Pencil className="w-3 h-3" />
                          </button>
                        </div>
                      )}
                      <p className="text-[10px] text-slate-400 font-mono">{identity}</p>
                    </div>
                  </div>

                  <div className="flex flex-col items-end gap-2">
                    {isActive ? (
                      <span className="text-[10px] bg-emerald-500/10 text-emerald-400 font-medium px-2 py-0.5 rounded-full border border-emerald-500/20 flex items-center gap-1">
                        <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
                        当前档案
                      </span>
                    ) : (
                      <button onClick={() => onSelectAccount(acct.id)} className="text-[10px] text-indigo-400 hover:text-indigo-300 font-semibold cursor-pointer underline hover:no-underline">
                        设为当前
                      </button>
                    )}
                    {!isActive && accounts.length > 1 && (
                      <button
                        type="button"
                        onClick={() => handleDeleteProfile(acct)}
                        className="text-[10px] text-slate-500 hover:text-rose-400 flex items-center gap-1 cursor-pointer"
                      >
                        <Trash2 className="w-3 h-3" />
                        删除
                      </button>
                    )}
                  </div>
                </div>

                <div className="p-4 space-y-3.5 text-xs text-slate-400">
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <span className="text-[10px] text-slate-500 block uppercase font-mono tracking-wider">手机号码</span>
                      <span className="text-slate-300 font-mono">{acct.phoneNumber || '-'}</span>
                    </div>
                    <div>
                      <span className="text-[10px] text-slate-500 block uppercase font-mono tracking-wider">Session 来源</span>
                      <span className="text-slate-300 font-mono truncate block text-[11px]">{hasSession ? acct.sessionName || 'saved_session' : '未保存'}</span>
                    </div>
                  </div>

                  {isActive && (
                    <div className="border-t border-slate-805 pt-3">
                      <button
                        type="button"
                        onClick={() => setSettingsAccountId(settingsAccountId === acct.id ? null : acct.id)}
                        className="w-full flex items-center justify-between gap-2 px-3 py-2 rounded-lg bg-slate-950/50 border border-slate-805 text-slate-300 hover:text-white hover:border-slate-700 transition-colors cursor-pointer"
                      >
                        <span className="flex items-center gap-2 text-[11px] font-semibold">
                          <Bot className="w-3.5 h-3.5 text-indigo-400" />
                          Bot 投递权限
                        </span>
                        <span className="flex items-center gap-1.5 text-[10px] text-slate-500">
                          {selectedAccessLabel}
                          {settingsAccountId === acct.id ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
                        </span>
                      </button>

                      {settingsAccountId === acct.id && (
                        <div className="mt-3 space-y-3 rounded-lg bg-slate-950/35 border border-slate-805 p-3 animate-fadeIn">
                          <div className="grid grid-cols-1 gap-2">
                            {accessModes.map((item) => {
                              const Icon = item.icon;
                              const active = botAccessMode === item.mode;
                              return (
                                <button
                                  key={item.mode}
                                  type="button"
                                  onClick={() => setBotAccessMode(item.mode)}
                                  className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-left transition-all cursor-pointer ${
                                    active
                                      ? 'bg-indigo-950/40 border-indigo-500/50 text-indigo-300'
                                      : 'bg-slate-950/50 border-slate-805 text-slate-500 hover:text-slate-300 hover:border-slate-700'
                                  }`}
                                >
                                  <Icon className="w-4 h-4 shrink-0" />
                                  <span className="min-w-0">
                                    <span className="block text-[11px] font-semibold">{item.label}</span>
                                    <span className="block text-[10px] opacity-70">{item.description}</span>
                                  </span>
                                </button>
                              );
                            })}
                          </div>

                          {botAccessMode === 'allowed' && (
                            <div className="space-y-1.5">
                              <label className="block text-[10px] text-slate-500 font-semibold">
                                指定用户 ID 或 @username
                              </label>
                              <textarea
                                id={`textarea-bot-allowed-users-${acct.id}`}
                                rows={4}
                                className="w-full bg-slate-950/80 border border-slate-800 focus:border-indigo-500 rounded-lg p-2 text-slate-300 font-mono focus:outline-none text-[11px] resize-none"
                                placeholder={'8906676091\n@telegram_user'}
                                value={allowedUsersText}
                                onChange={(event) => setAllowedUsersText(event.target.value)}
                              />
                            </div>
                          )}

                          <div className="flex items-center justify-between gap-2">
                            <span className="text-[10px] text-slate-500">
                              非所有者只能私聊投递媒体或链接
                            </span>
                            <button
                              type="button"
                              disabled={loading}
                              onClick={handleSaveBotAccess}
                              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 disabled:opacity-60 text-white text-[10px] font-bold transition-all cursor-pointer"
                            >
                              {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
                              保存
                            </button>
                          </div>

                          {accessFeedback && (
                            <div className="text-[10px] text-emerald-400 flex items-center gap-1.5">
                              <CheckCircle2 className="w-3.5 h-3.5" />
                              {accessFeedback}
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  )}

                  <div className="flex items-center justify-between border-t border-slate-805 pt-3.5">
                    <div className={`text-[10px] flex items-center gap-1 ${isRunning ? 'text-emerald-400' : hasSession ? 'text-indigo-400' : 'text-slate-500'}`}>
                      <CheckCircle2 className="w-3.5 h-3.5" />
                      {isRunning ? '运行中' : hasSession ? '已保存 Session' : '未登录'}
                    </div>
                    <div className="flex items-center gap-2">
                      {hasSession && !isRunning && (
                        <button
                          onClick={() => run(() => Promise.resolve(onStartAccount(acct.id)))}
                          className="text-[10px] text-indigo-400 hover:text-indigo-300 flex items-center gap-1.5 transition-colors cursor-pointer"
                        >
                          <ShieldCheck className="w-3.5 h-3.5" />
                          启动
                        </button>
                      )}
                      {hasSession && isRunning && (
                        <button
                          onClick={() => run(() => Promise.resolve(onStopAccount(acct.id)))}
                          className="text-[10px] text-amber-400 hover:text-amber-300 flex items-center gap-1.5 transition-colors cursor-pointer"
                        >
                          <LogOut className="w-3.5 h-3.5" />
                          停止
                        </button>
                      )}
                      {!hasSession && (
                        <button
                          onClick={() => startLogin(acct.id, acct.phoneNumber)}
                          className="text-[10px] text-indigo-400 hover:text-indigo-300 flex items-center gap-1.5 transition-colors cursor-pointer"
                        >
                          <UserPlus className="w-3.5 h-3.5" />
                          登录
                        </button>
                      )}
                      {hasSession && (
                        <button
                          onClick={() => run(() => Promise.resolve(onDisconnectAccount(acct.id)))}
                          className="text-[10px] text-slate-500 hover:text-rose-400 flex items-center gap-1.5 transition-colors cursor-pointer"
                        >
                          <LogOut className="w-3.5 h-3.5" />
                          清除此会话
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              </div>
            );
          })}

          {accounts.length === 0 && (
            <div className="col-span-full py-12 text-center space-y-3 border border-dashed border-slate-800 rounded-xl bg-slate-900/30">
              <ShieldAlert className="w-10 h-10 text-slate-600 mx-auto" />
              <div className="space-y-1">
                <p className="text-xs text-slate-300 font-medium">当前没有运行中的 Telegram Session</p>
                <p className="text-[10px] text-slate-550 max-w-sm mx-auto">
                  可以连接保存的数据库 session，或用手机号重新完成 Telegram 登录。
                </p>
              </div>
              {sessionExists && (
                <button
                  onClick={() => run(onConnectSavedSession)}
                  className="inline-flex items-center justify-center gap-1.5 px-4 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold"
                >
                  <ShieldCheck className="w-4 h-4" />
                  立即连接已保存 Session
                </button>
              )}
            </div>
          )}
        </div>
      )}

      {step === 'phone' && (
        <form onSubmit={handleSendCode} className="max-w-md mx-auto bg-slate-900 border border-slate-800 p-6 rounded-xl shadow-xl space-y-5 animate-fadeIn">
          <div className="border-b border-slate-850 pb-3.5">
            <h3 className="text-xs font-semibold text-slate-200">
              {loginTargetAccount ? `登录到：${profileLabel(loginTargetAccount)}` : '第 1 步：填写手机号'}
            </h3>
            <p className="text-[10px] text-slate-500">
              {loginTargetAccount ? '成功后会把 Session 保存到此账号档案。' : '验证码将发送到你的 Telegram 客户端。'}
            </p>
          </div>

          <label className="flex items-center gap-2 text-slate-450 cursor-pointer select-none text-xs">
            <input
              type="checkbox"
              checked={useConfiguredApi}
              className="rounded border-slate-700 bg-slate-900 text-indigo-600 focus:ring-0 w-3.5 h-3.5"
              onChange={(event) => setUseConfiguredApi(event.target.checked)}
            />
            使用当前配置中的 api_id / api_hash
          </label>

          {!useConfiguredApi && (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 animate-fadeIn">
              <input className="w-full bg-slate-950 border border-slate-800 rounded-lg p-2.5 text-slate-300 font-mono focus:outline-none text-xs" placeholder="api_id" value={apiId} onChange={(event) => setApiId(event.target.value)} />
              <input className="w-full bg-slate-950 border border-slate-800 rounded-lg p-2.5 text-slate-300 font-mono focus:outline-none text-xs" placeholder="api_hash" value={apiHash} onChange={(event) => setApiHash(event.target.value)} />
            </div>
          )}

          <div className="space-y-1">
            <label className="block text-slate-400 font-medium text-xs">手机号</label>
            <div className="relative">
              <span className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                <Smartphone className="w-4 h-4 text-slate-500" />
              </span>
              <input
                type="text"
                required
                className="w-full pl-9 pr-3 py-2.5 bg-slate-950 border border-slate-800 focus:border-indigo-500 rounded-lg text-slate-300 font-mono focus:outline-none text-xs"
                placeholder="+86 138 0000 0000"
                value={phoneNumber}
                onChange={(event) => setPhoneNumber(event.target.value)}
              />
            </div>
          </div>

          <div className="flex gap-3 pt-3 border-t border-slate-850">
            <button
              type="button"
              onClick={() => {
                setLoginTargetProfileId(null);
                setStep('list');
              }}
              className="flex-1 py-2 bg-slate-800 hover:bg-slate-750 text-slate-300 rounded-lg text-xs font-semibold cursor-pointer"
            >
              取消
            </button>
            <button type="submit" disabled={loading} className="flex-1 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-xs font-semibold flex items-center justify-center gap-1 transition-all cursor-pointer">
              {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <>发送验证码 <ArrowRight className="w-3.5 h-3.5" /></>}
            </button>
          </div>
        </form>
      )}

      {step === 'code' && (
        <form onSubmit={handleVerifyCode} className="max-w-md mx-auto bg-slate-900 border border-slate-800 p-6 rounded-xl shadow-xl space-y-5 animate-fadeIn">
          <div className="text-center space-y-1.5 bg-slate-950/50 p-4 rounded-lg border border-indigo-950/40">
            <p className="text-slate-400 font-mono text-xs">验证码已发送</p>
            <p className="text-indigo-400 font-bold font-mono text-[13px]">{phoneNumber}</p>
          </div>
          <input
            type="text"
            maxLength={6}
            required
            placeholder="验证码"
            className="w-full text-center bg-slate-950 border border-slate-800 focus:border-indigo-500 tracking-widest text-[16px] font-mono font-bold rounded-lg p-3 text-slate-100 focus:outline-none"
            value={code}
            onChange={(event) => setCode(event.target.value)}
          />
          <div className="flex gap-3 pt-3 border-t border-slate-850">
            <button type="button" onClick={() => setStep('phone')} className="flex-1 py-2 bg-slate-800 hover:bg-slate-750 text-slate-300 rounded-lg text-xs font-semibold cursor-pointer">
              上一步
            </button>
            <button type="submit" disabled={loading} className="flex-1 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-xs font-semibold flex items-center justify-center gap-1 transition-all cursor-pointer">
              {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <>验证 <ArrowRight className="w-3.5 h-3.5" /></>}
            </button>
          </div>
        </form>
      )}

      {step === 'password' && (
        <form onSubmit={handleVerifyPassword} className="max-w-md mx-auto bg-slate-900 border border-slate-800 p-6 rounded-xl shadow-xl space-y-5 animate-fadeIn">
          <div className="text-center space-y-1.5 bg-yellow-500/5 p-4 rounded-lg border border-yellow-500/10">
            <ShieldCheck className="w-6 h-6 text-yellow-500 mx-auto" />
            <p className="text-slate-300 font-semibold font-mono text-xs">此账户已启用二步验证</p>
          </div>
          <input
            type="password"
            required
            placeholder="Telegram 二步验证密码"
            className="w-full bg-slate-950 border border-slate-800 focus:border-indigo-500 text-xs font-mono rounded-lg p-3 text-slate-100 focus:outline-none"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
          <button type="submit" disabled={loading} className="w-full py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-xs font-semibold flex items-center justify-center gap-1 transition-all cursor-pointer">
            {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <>完成登录 <ArrowRight className="w-3.5 h-3.5" /></>}
          </button>
        </form>
      )}
    </div>
  );
}
