// SPDX-AI-Disclosure: ai-generated
using Game.Features.Boss;
using NUnit.Framework;

namespace StandaloneCore.Tests
{
    /// <summary>
    /// issue_2 の受入テスト。突進の前に挟む「タメ（予備動作）」の決定論的な検証。
    ///
    /// 実装は Game/Assets/Core に置き、UnityEngine を参照しない。
    /// 時間は Update(float dt) で外から注入するので、Unity を起動せずに
    /// 閾値の前後をミリ秒単位で確かめられる。
    ///
    /// この単位が定める閾値の扱い（Issue 本文には書かれていないので、ここで決める）:
    ///   ElapsedSeconds >= ChargeSeconds となった Update の時点で Dashing に移る。
    ///   したがって「ちょうど 0.2 秒」は突進側に入る。
    /// </summary>
    public class BossChargeStateTests
    {
        private const float Tol = 1e-6f;

        // ---- 基本の状態遷移 ---------------------------------------------------

        [Test]
        public void Idle_BeforeBeginCharge_NeverDashes()
        {
            var s = new BossChargeState();

            s.Update(10f);

            Assert.AreEqual(BossChargePhase.Idle, s.Phase, "BeginCharge を呼ぶ前に突進してはならない");
            Assert.AreEqual(0f, s.ElapsedSeconds, Tol, "Idle ではタイマーが進んではならない");
        }

        [Test]
        public void BeginCharge_EntersChargingWithEmptyTimer()
        {
            var s = new BossChargeState();

            s.BeginCharge();

            Assert.AreEqual(BossChargePhase.Charging, s.Phase, "BeginCharge 直後は必ずタメ");
            Assert.AreEqual(0f, s.ElapsedSeconds, Tol);
        }

        // ---- 満足の基準 1: 0.1 秒進めてもまだタメ -----------------------------

        [Test]
        public void Update_ZeroPointOneSecond_StaysCharging()
        {
            var s = new BossChargeState();
            s.BeginCharge();

            s.Update(0.1f);

            Assert.AreEqual(BossChargePhase.Charging, s.Phase, "0.1 秒ではまだタメの状態のままであること");
            Assert.AreEqual(0.1f, s.ElapsedSeconds, Tol);
        }

        [Test]
        public void Update_AccumulatedBelowThreshold_StaysCharging()
        {
            var s = new BossChargeState();
            s.BeginCharge();

            s.Update(0.05f);
            s.Update(0.05f);
            s.Update(0.05f);

            Assert.AreEqual(BossChargePhase.Charging, s.Phase, "積算 0.15 秒は閾値未満");
            Assert.AreEqual(0.15f, s.ElapsedSeconds, Tol);
        }

        [Test]
        public void Update_JustBelowThreshold_StaysCharging()
        {
            var s = new BossChargeState();
            s.BeginCharge();

            s.Update(0.19f);

            Assert.AreEqual(BossChargePhase.Charging, s.Phase, "0.19 秒は閾値未満");
        }

        // ---- 閾値ちょうど -----------------------------------------------------

        [Test]
        public void Update_ExactlyAtThreshold_EntersDashing()
        {
            var s = new BossChargeState();
            s.BeginCharge();

            s.Update(0.2f);

            Assert.AreEqual(BossChargePhase.Dashing, s.Phase, "ちょうど 0.2 秒で突進に入る");
        }

        [Test]
        public void Update_AccumulatedExactlyAtThreshold_EntersDashing()
        {
            var s = new BossChargeState();
            s.BeginCharge();

            s.Update(0.1f);
            Assert.AreEqual(BossChargePhase.Charging, s.Phase, "0.1 秒の時点ではタメ");

            s.Update(0.1f);
            Assert.AreEqual(BossChargePhase.Dashing, s.Phase, "積算でちょうど 0.2 秒に達したら突進");
        }

        // ---- 満足の基準 2: 0.2 秒を超えたら突進 -------------------------------

        [Test]
        public void Update_AboveThreshold_EntersDashing()
        {
            var s = new BossChargeState();
            s.BeginCharge();

            s.Update(0.25f);

            Assert.AreEqual(BossChargePhase.Dashing, s.Phase, "0.2 秒を超えたとき突進の状態に移ること");
        }

        [Test]
        public void Update_CrossesThresholdInSecondStep_EntersDashing()
        {
            var s = new BossChargeState();
            s.BeginCharge();

            s.Update(0.19f);
            Assert.AreEqual(BossChargePhase.Charging, s.Phase, "0.19 秒ではまだタメ");

            s.Update(0.02f);
            Assert.AreEqual(BossChargePhase.Dashing, s.Phase, "0.21 秒で突進");
        }

        // ---- 満足の基準 3: タメの長さを外から設定できる -----------------------

        [Test]
        public void DefaultChargeSeconds_IsZeroPointTwo()
        {
            var s = new BossChargeState();

            Assert.AreEqual(0.2f, BossChargeState.DefaultChargeSeconds, Tol, "既定のタメは 0.2 秒");
            Assert.AreEqual(0.2f, s.ChargeSeconds, Tol, "既定コンストラクタは既定値を使う");
        }

        [Test]
        public void LongerChargeSeconds_DelaysDash()
        {
            var s = new BossChargeState(0.5f);
            s.BeginCharge();

            s.Update(0.3f);
            Assert.AreEqual(BossChargePhase.Charging, s.Phase, "0.5 秒設定なら 0.3 秒はまだタメ");

            s.Update(0.25f);
            Assert.AreEqual(BossChargePhase.Dashing, s.Phase, "積算 0.55 秒で閾値超過");
            Assert.AreEqual(0.5f, s.ChargeSeconds, Tol, "設定した長さがそのまま保持されること");
        }

        [Test]
        public void ShorterChargeSeconds_DashesEarlierThanDefault()
        {
            var quick = new BossChargeState(0.05f);
            var normal = new BossChargeState();
            quick.BeginCharge();
            normal.BeginCharge();

            quick.Update(0.06f);
            normal.Update(0.06f);

            Assert.AreEqual(BossChargePhase.Dashing, quick.Phase, "0.05 秒設定なら 0.06 秒で突進");
            Assert.AreEqual(BossChargePhase.Charging, normal.Phase, "既定 0.2 秒なら 0.06 秒ではまだタメ");
        }

        // ---- 異常系 -----------------------------------------------------------

        [Test]
        public void ZeroChargeSeconds_DashesOnFirstUpdate()
        {
            var s = new BossChargeState(0f);

            s.BeginCharge();
            Assert.AreEqual(BossChargePhase.Charging, s.Phase, "長さ 0 でも BeginCharge 直後はタメ");

            s.Update(0f);
            Assert.AreEqual(BossChargePhase.Dashing, s.Phase, "長さ 0 なら最初の Update で突進");
        }

        [Test]
        public void NegativeChargeSeconds_IsClampedToZero()
        {
            var s = new BossChargeState(-1f);

            Assert.AreEqual(0f, s.ChargeSeconds, Tol, "負のタメ長は 0 に丸める");

            s.BeginCharge();
            s.Update(0f);
            Assert.AreEqual(BossChargePhase.Dashing, s.Phase, "0 に丸めた後は最初の Update で突進");
        }

        [Test]
        public void ZeroDelta_DoesNotAdvanceTimer()
        {
            var s = new BossChargeState();
            s.BeginCharge();

            for (int i = 0; i < 5; i++)
            {
                s.Update(0f);
            }

            Assert.AreEqual(BossChargePhase.Charging, s.Phase, "dt = 0 を何度呼んでも突進しない");
            Assert.AreEqual(0f, s.ElapsedSeconds, Tol, "dt = 0 でタイマーが進んではならない");
        }

        [Test]
        public void NegativeDelta_DoesNotRewindTimer()
        {
            var s = new BossChargeState();
            s.BeginCharge();
            s.Update(0.1f);

            s.Update(-5f);

            Assert.AreEqual(BossChargePhase.Charging, s.Phase);
            Assert.AreEqual(0.1f, s.ElapsedSeconds, Tol, "負の dt は無視する。巻き戻してはならない");

            s.Update(0.1f);
            Assert.AreEqual(BossChargePhase.Dashing, s.Phase, "巻き戻していれば、ここで突進に入らない");
        }

        [Test]
        public void HugeDelta_EntersDashingAndKeepsTimerFinite()
        {
            var s = new BossChargeState();
            s.BeginCharge();

            s.Update(int.MaxValue);
            s.Update(int.MaxValue);

            Assert.AreEqual(BossChargePhase.Dashing, s.Phase, "巨大な dt でも突進に入るだけ");
            Assert.IsFalse(float.IsNaN(s.ElapsedSeconds), "経過時間が NaN になってはならない");
            Assert.IsFalse(float.IsInfinity(s.ElapsedSeconds), "経過時間が無限大になってはならない");
        }

        [Test]
        public void IntMinValueDelta_IsIgnored()
        {
            var s = new BossChargeState();
            s.BeginCharge();

            s.Update(int.MinValue);

            Assert.AreEqual(BossChargePhase.Charging, s.Phase, "巨大な負値でも突進してはならない");
            Assert.AreEqual(0f, s.ElapsedSeconds, Tol, "巨大な負値でタイマーが動いてはならない");
        }

        [Test]
        public void NonFiniteDelta_IsIgnored()
        {
            var s = new BossChargeState();
            s.BeginCharge();

            s.Update(float.NaN);
            s.Update(float.PositiveInfinity);
            s.Update(float.NegativeInfinity);

            Assert.AreEqual(BossChargePhase.Charging, s.Phase, "NaN と無限大は無視する");
            Assert.AreEqual(0f, s.ElapsedSeconds, Tol, "NaN を積算してタイマーを壊してはならない");

            s.Update(0.2f);
            Assert.AreEqual(BossChargePhase.Dashing, s.Phase, "無視した後も通常の積算が続くこと");
        }

        // ---- 突進に入った後 ---------------------------------------------------

        [Test]
        public void Dashing_IsTerminalUntilBeginChargeOrReset()
        {
            var s = new BossChargeState();
            s.BeginCharge();
            s.Update(0.2f);
            float elapsedAtDash = s.ElapsedSeconds;

            s.Update(1f);

            Assert.AreEqual(BossChargePhase.Dashing, s.Phase, "突進に入った後にタメへ戻ってはならない");
            Assert.AreEqual(elapsedAtDash, s.ElapsedSeconds, Tol, "突進後はタメのタイマーを進めない");
        }

        [Test]
        public void BeginCharge_WhileDashing_RestartsTheWindup()
        {
            var s = new BossChargeState();
            s.BeginCharge();
            s.Update(0.2f);

            s.BeginCharge();

            Assert.AreEqual(BossChargePhase.Charging, s.Phase, "次の突進にも必ずタメが要る");
            Assert.AreEqual(0f, s.ElapsedSeconds, Tol, "タイマーは積み残さず 0 から始める");

            s.Update(0.1f);
            Assert.AreEqual(BossChargePhase.Charging, s.Phase, "積み残していれば、ここで突進してしまう");
        }

        [Test]
        public void Reset_ReturnsToIdleAndStopsTheTimer()
        {
            var s = new BossChargeState();
            s.BeginCharge();
            s.Update(0.1f);

            s.Reset();

            Assert.AreEqual(BossChargePhase.Idle, s.Phase, "Reset で待機に戻る");
            Assert.AreEqual(0f, s.ElapsedSeconds, Tol);

            s.Update(10f);
            Assert.AreEqual(BossChargePhase.Idle, s.Phase, "Reset 後は BeginCharge 無しに突進しない");
        }
    }
}
