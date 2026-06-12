/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from 'react';
import {
  AlertCircle,
  ArrowRight,
  CheckCircle2,
  Loader2,
  LogOut,
  Plus,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
  Smartphone,
  Users,
} from 'lucide-react';
import { TelegramAccount } from '../types';

interface AccountManagerProps {
  accounts: TelegramAccount[];
  activeAccountId: string | null;
  sessionExists: boolean;
  onSelectAccount: (id: string) => void;
  onDisconnectAccount: (id: string) => Promise<void> | void;
  onConnectSavedSession: () => Promise<void>;
  onSendCode: (phoneNumber: string, apiId?: string, apiHash?: string) => Promise<void>;
  onVerifyCode: (code: string) => Promise<{ needsPassword: boolean }>;
  onVerifyPassword: (password: string) => Promise<void>;
  onRefresh: () => Promise<void>;
}

export function AccountManager({
  accounts,
  activeAccountId,
  sessionExists,
  onSelectAccount,
  onDisconnectAccount,
  onConnectSavedSession,
  onSendCode,
  onVerifyCode,
  onVerifyPassword,
  onRefresh,
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
      await onSendCode(phoneNumber, useConfiguredApi ? undefined : apiId, useConfiguredApi ? undefined : apiHash);
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
              onClick={() => {
                setErrorMessage('');
                setStep('phone');
              }}
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
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {accounts.map((acct) => {
            const isActive = activeAccountId === acct.id;
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
                      {(acct.firstName || acct.username || 'TG').substring(0, 2).toUpperCase()}
                    </div>
                    <div>
                      <h3 className="text-xs font-semibold text-slate-200">{acct.firstName || 'Telegram Session'}</h3>
                      <p className="text-[10px] text-slate-400 font-mono">{acct.username || acct.id}</p>
                    </div>
                  </div>

                  {isActive ? (
                    <span className="text-[10px] bg-emerald-500/10 text-emerald-400 font-medium px-2 py-0.5 rounded-full border border-emerald-500/20 flex items-center gap-1">
                      <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
                      主会话
                    </span>
                  ) : (
                    <button onClick={() => onSelectAccount(acct.id)} className="text-[10px] text-indigo-400 hover:text-indigo-300 font-semibold cursor-pointer underline hover:no-underline">
                      设为当前
                    </button>
                  )}
                </div>

                <div className="p-4 space-y-3.5 text-xs text-slate-400">
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <span className="text-[10px] text-slate-500 block uppercase font-mono tracking-wider">手机号码</span>
                      <span className="text-slate-300 font-mono">{acct.phoneNumber || '-'}</span>
                    </div>
                    <div>
                      <span className="text-[10px] text-slate-500 block uppercase font-mono tracking-wider">Session 来源</span>
                      <span className="text-slate-300 font-mono truncate block text-[11px]">{acct.sessionName || 'database_session'}</span>
                    </div>
                  </div>

                  <div className="flex items-center justify-between border-t border-slate-805 pt-3.5">
                    <div className="text-[10px] text-emerald-400 flex items-center gap-1">
                      <CheckCircle2 className="w-3.5 h-3.5" />
                      已连接
                    </div>
                    <button
                      onClick={() => run(() => Promise.resolve(onDisconnectAccount(acct.id)))}
                      className="text-[10px] text-slate-500 hover:text-rose-400 flex items-center gap-1.5 transition-colors cursor-pointer"
                    >
                      <LogOut className="w-3.5 h-3.5" />
                      断开此会话
                    </button>
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
            <h3 className="text-xs font-semibold text-slate-200">第 1 步：填写手机号</h3>
            <p className="text-[10px] text-slate-500">验证码将发送到你的 Telegram 客户端。</p>
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
            <button type="button" onClick={() => setStep('list')} className="flex-1 py-2 bg-slate-800 hover:bg-slate-750 text-slate-300 rounded-lg text-xs font-semibold cursor-pointer">
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
