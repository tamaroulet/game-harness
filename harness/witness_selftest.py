"""証拠の状態（witness）の生成物を、仮のゲーム（スタブ）でコンパイルして走らせる自己検査の置き場を作る。

    python -m harness.witness_selftest <出力先>
    dotnet test <出力先>/W.csproj --filter "FullyQualifiedName~P_T5_0&FullyQualifiedName!~P_T5_01"

**なぜ要るか**: 総監督は dotnet のビルドとテストを走らせない（防壁①）。propgen の証拠の状態の C#（RunWitnesses・
Witnesses_…）が、コンパイルでき、復元 → 1 Tick → then の検査の順に動くことを、操縦士が一度だけ確かめる。
ゲームのリポジトリ（falling-blocks）には触れない。出力先は一時ディレクトリにする。

中身：tests/test_witness.py と同じ宣言（I と O の 2 種の小さな仕様。証拠の状態は、一番下の行の右端の穴に縦の I を
ハードドロップする）から作った生成物と、ハードドロップと固定だけを持つスタブの GameState。

期待する結果：`P_T5_02_Public` が合格（証拠の状態で前提が 1 回成り立ち、固定の数が 1 増える）。`P_T5_02_Hidden` は
非公開シードが無いので Ignore（HIDDEN_SEEDS_ABSENT）。`P_T5_03_Public`・`P_T5_03_Hidden` は複数ティック（3 Tick）で、どちらも合格
（無作為な系列を流さないので非公開シードに依らない）。`P_T5_01` は I と O だけのスタブでは確かめないので、フィルタで外す。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
sys.path.insert(0, str(ROOT / "tests"))

import exitcode  # noqa: E402

STUB = """using System.Collections.Generic;
namespace Game.Core
{
    public enum GamePhase { Ready, Playing, GameOver }
    public enum MinoType { I, O }
    public enum Rotation { Spawn, Right, Two, Left }

    public readonly struct ActiveMino
    {
        public ActiveMino(MinoType type, int x, int y, Rotation rotation) { Type = type; X = x; Y = y; Rotation = rotation; }
        public MinoType Type { get; }
        public int X { get; }
        public int Y { get; }
        public Rotation Rotation { get; }
    }

    public readonly struct TickInput
    {
        public TickInput(bool left, bool right, bool rotateCw, bool rotateCcw, bool softDrop, bool hardDrop)
        {
            Left = left; Right = right; RotateCw = rotateCw; RotateCcw = rotateCcw; SoftDrop = softDrop; HardDrop = hardDrop;
        }
        public bool Left { get; }
        public bool Right { get; }
        public bool RotateCw { get; }
        public bool RotateCcw { get; }
        public bool SoftDrop { get; }
        public bool HardDrop { get; }
    }

    public readonly struct Cell
    {
        public Cell(int x, int y) { X = x; Y = y; }
        public int X { get; }
        public int Y { get; }
    }

    // スタブ：復元・ハードドロップ・固定だけ（自己検査用。ゲームの実装ではない）
    public sealed class GameState
    {
        static readonly int[][][][] Shapes =
        {
            new[] { new[] { new[] { 0, 2 }, new[] { 1, 2 }, new[] { 2, 2 }, new[] { 3, 2 } },
                    new[] { new[] { 2, 3 }, new[] { 2, 2 }, new[] { 2, 1 }, new[] { 2, 0 } },
                    new[] { new[] { 0, 1 }, new[] { 1, 1 }, new[] { 2, 1 }, new[] { 3, 1 } },
                    new[] { new[] { 1, 3 }, new[] { 1, 2 }, new[] { 1, 1 }, new[] { 1, 0 } } },
            new[] { new[] { new[] { 1, 1 }, new[] { 2, 1 }, new[] { 1, 2 }, new[] { 2, 2 } },
                    new[] { new[] { 1, 1 }, new[] { 2, 1 }, new[] { 1, 2 }, new[] { 2, 2 } },
                    new[] { new[] { 1, 1 }, new[] { 2, 1 }, new[] { 1, 2 }, new[] { 2, 2 } },
                    new[] { new[] { 1, 1 }, new[] { 2, 1 }, new[] { 1, 2 }, new[] { 2, 2 } } },
        };
        readonly bool[,] occ = new bool[10, 22];

        public GameState(GamePhase phase, ActiveMino? activeMino, IReadOnlyList<Cell> locked)
        {
            Phase = phase; ActiveMino = activeMino;
            foreach (var c in locked) occ[c.X, c.Y] = true;
        }

        public GamePhase Phase { get; private set; }
        public ActiveMino? ActiveMino { get; private set; }
        public IReadOnlyList<MinoType> NextQueue => new List<MinoType>();
        public int LockedMinoCount { get; private set; }
        public bool IsOccupied(int x, int y) => x < 0 || y < 0 || x >= 10 || y >= 22 || occ[x, y];

        bool Fits(ActiveMino m)
        {
            foreach (var c in Shapes[(int)m.Type][(int)m.Rotation])
                if (IsOccupied(m.X + c[0], m.Y + c[1])) return false;
            return true;
        }

        public void Tick(TickInput input)
        {
            if (ActiveMino == null || Phase != GamePhase.Playing || !input.HardDrop) return;
            var m = ActiveMino.Value;
            while (Fits(new ActiveMino(m.Type, m.X, m.Y - 1, m.Rotation))) m = new ActiveMino(m.Type, m.X, m.Y - 1, m.Rotation);
            foreach (var c in Shapes[(int)m.Type][(int)m.Rotation]) occ[m.X + c[0], m.Y + c[1]] = true;
            LockedMinoCount++;
            ActiveMino = new ActiveMino(MinoType.O, 3, 19, Rotation.Spawn);
        }
    }
}
"""

CSPROJ = """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
    <Nullable>enable</Nullable>
    <IsPackable>false</IsPackable>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Microsoft.NET.Test.Sdk" Version="17.8.0" />
    <PackageReference Include="NUnit" Version="3.14.0" />
    <PackageReference Include="NUnit3TestAdapter" Version="4.5.0" />
  </ItemGroup>
</Project>
"""


def files():
    """{相対パス: 本文}。決定論。"""
    import propgen
    import test_propgen as tp
    import test_witness as tw
    decl = tw.decl_with()
    # 複数ティックの性質（ticks）：最初の Tick にハードドロップ、あと 2 Tick は入力なし。固定は最初の Tick の 1 回だけ
    decl["properties"].append(dict(decl["properties"][-1], id="P-T5-03", ticks=3))
    out = {f"gen/{k.split('/', 1)[1]}": v
           for k, v in propgen.generate(decl, tp.SPEC, tp.GDD, tp.INTERFACE, "x", report_hits=True).items()}
    out["Stub.cs"] = STUB
    out["W.csproj"] = CSPROJ
    return out


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print(__doc__)
        return 2
    dest = Path(argv[0])
    if dest.exists() and any(dest.iterdir()):
        print(f"NG: 出力先が空ではありません: {dest}")
        return 2
    for rel, text in files().items():
        p = dest / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")
    print(f"書き出し: {dest}")
    print(f'次に: dotnet test "{dest / "W.csproj"}" --filter "FullyQualifiedName~P_T5_0&FullyQualifiedName!~P_T5_01"')
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(exitcode.normalized(main))
