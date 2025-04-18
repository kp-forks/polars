from __future__ import annotations

from datetime import date, datetime, time
from functools import reduce
from operator import add
from string import ascii_letters
from typing import Any, cast

import numpy as np
import pytest
from _pytest.capture import CaptureFixture

import polars as pl
from polars import col, lit, when
from polars.datatypes import NUMERIC_DTYPES, PolarsDataType
from polars.testing import assert_frame_equal
from polars.testing.asserts import assert_series_equal


def test_lazy() -> None:
    df = pl.DataFrame({"a": [1, 2, 3], "b": [1.0, 2.0, 3.0]})
    _ = df.lazy().with_columns(pl.lit(1).alias("foo")).select([col("a"), col("foo")])

    # test if it executes
    _ = (
        df.lazy()
        .with_columns(
            when(pl.col("a") > pl.lit(2))
            .then(pl.lit(10))
            .otherwise(pl.lit(1))
            .alias("new")
        )
        .collect()
    )

    # test if pl.list is available, this is `to_list` re-exported as list
    eager = df.groupby("a").agg(pl.list("b"))
    assert sorted(eager.rows()) == [(1, [[1.0]]), (2, [[2.0]]), (3, [[3.0]])]

    # profile lazyframe operation/plan
    lazy = df.lazy().groupby("a").agg(pl.list("b"))
    profiling_info = lazy.profile()
    # ┌──────────────┬───────┬─────┐
    # │ node         ┆ start ┆ end │
    # │ ---          ┆ ---   ┆ --- │
    # │ str          ┆ u64   ┆ u64 │
    # ╞══════════════╪═══════╪═════╡
    # │ optimization ┆ 0     ┆ 69  │
    # │ groupby(a)   ┆ 69    ┆ 342 │
    # └──────────────┴───────┴─────┘
    assert len(profiling_info) == 2
    assert profiling_info[1].columns == ["node", "start", "end"]


def test_lazyframe_membership_operator() -> None:
    ldf = pl.DataFrame({"name": ["Jane", "John"], "age": [20, 30]}).lazy()
    assert "name" in ldf
    assert "phone" not in ldf

    # note: cannot use lazyframe in boolean context
    with pytest.raises(ValueError, match="ambiguous"):
        not ldf


def test_apply() -> None:
    df = pl.DataFrame({"a": [1, 2, 3], "b": [1.0, 2.0, 3.0]})
    new = df.lazy().with_columns(col("a").map(lambda s: s * 2).alias("foo")).collect()

    expected = df.clone().with_columns((pl.col("a") * 2).alias("foo"))
    assert_frame_equal(new, expected)


def test_add_eager_column() -> None:
    ldf = pl.DataFrame({"a": [1, 2, 3], "b": [1.0, 2.0, 3.0]}).lazy()
    assert ldf.width == 2

    out = ldf.with_columns(pl.lit(pl.Series("c", [1, 2, 3]))).collect()
    assert out["c"].sum() == 6
    assert out.width == 3


def test_set_null() -> None:
    df = pl.DataFrame({"a": [1, 2, 3], "b": [1.0, 2.0, 3.0]})
    out = (
        df.lazy()
        .with_columns(when(col("a") > 1).then(lit(None)).otherwise(100).alias("foo"))
        .collect()
    )
    s = out["foo"]
    assert s[0] == 100
    assert s[1] is None
    assert s[2] is None


def test_take_every() -> None:
    df = pl.DataFrame({"a": [1, 2, 3, 4], "b": ["w", "x", "y", "z"]}).lazy()
    expected_df = pl.DataFrame({"a": [1, 3], "b": ["w", "y"]})
    assert_frame_equal(expected_df, df.take_every(2).collect())


def test_slice() -> None:
    ldf = pl.DataFrame({"a": [1, 2, 3, 4], "b": ["a", "b", "c", "d"]}).lazy()
    expected = pl.DataFrame({"a": [3, 4], "b": ["c", "d"]}).lazy()
    for slice_params in (
        [2, 10],  # slice > len(df)
        [2, 4],  # slice == len(df)
        [2],  # optional len
    ):
        assert_frame_equal(ldf.slice(*slice_params), expected)

    for py_slice in (
        slice(1, 2),
        slice(0, 3, 2),
        slice(-3, None),
        slice(None, 2, 2),
        slice(3, None, -1),
        slice(1, None, -2),
    ):
        # confirm frame slice matches python slice
        assert ldf[py_slice].collect().rows() == ldf.collect().rows()[py_slice]

    assert_frame_equal(ldf[::-1], ldf.reverse())
    assert_frame_equal(ldf[::-2], ldf.reverse().take_every(2))


def test_agg() -> None:
    df = pl.DataFrame({"a": [1, 2, 3], "b": [1.0, 2.0, 3.0]})
    ldf = df.lazy().min()
    res = ldf.collect()
    assert res.shape == (1, 2)
    assert res.row(0) == (1, 1.0)


def test_or() -> None:
    df = pl.DataFrame({"a": [1, 2, 3], "b": [1.0, 2.0, 3.0]})
    out = df.lazy().filter((pl.col("a") == 1) | (pl.col("b") > 2)).collect()
    assert out.rows() == [(1, 1.0), (3, 3.0)]


def test_groupby_apply() -> None:
    df = pl.DataFrame({"a": [1, 1, 3], "b": [1.0, 2.0, 3.0]})
    ldf = (
        df.lazy()
        .groupby("a")
        .apply(lambda df: df * 2.0, schema={"a": pl.Float64, "b": pl.Float64})
    )
    out = ldf.collect()
    assert out.schema == ldf.schema
    assert out.shape == (3, 2)


def test_filter_str() -> None:
    # use a str instead of a column expr
    df = pl.DataFrame(
        {
            "time": ["11:11:00", "11:12:00", "11:13:00", "11:14:00"],
            "bools": [True, False, True, False],
        }
    )
    q = df.lazy()

    # last row based on a filter
    result = q.filter(pl.col("bools")).select(pl.last("*")).collect()
    expected = pl.DataFrame({"time": ["11:13:00"], "bools": [True]})
    assert_frame_equal(result, expected)

    # last row based on a filter
    result = q.filter("bools").select(pl.last("*")).collect()
    assert_frame_equal(result, expected)


def test_apply_custom_function() -> None:
    df = pl.DataFrame(
        {
            "A": [1, 2, 3, 4, 5],
            "fruits": ["banana", "banana", "apple", "apple", "banana"],
            "B": [5, 4, 3, 2, 1],
            "cars": ["beetle", "audi", "beetle", "beetle", "beetle"],
        }
    )

    # two ways to determine the length groups.
    a = (
        df.lazy()
        .groupby("fruits")
        .agg(
            [
                pl.col("cars")
                .apply(lambda groups: groups.len(), return_dtype=pl.Int64)
                .alias("custom_1"),
                pl.col("cars")
                .apply(lambda groups: groups.len(), return_dtype=pl.Int64)
                .alias("custom_2"),
                pl.count("cars").alias("cars_count"),
            ]
        )
        .sort("custom_1", reverse=True)
    ).collect()
    expected = pl.DataFrame(
        {
            "fruits": ["banana", "apple"],
            "custom_1": [3, 2],
            "custom_2": [3, 2],
            "cars_count": [3, 2],
        }
    )
    expected = expected.with_columns(pl.col("cars_count").cast(pl.UInt32))
    assert_frame_equal(a, expected)


def test_groupby() -> None:
    df = pl.DataFrame({"a": [1.0, None, 3.0, 4.0], "groups": ["a", "a", "b", "b"]})

    expected = pl.DataFrame({"groups": ["a", "b"], "a": [1.0, 3.5]})

    out = df.lazy().groupby("groups").agg(pl.mean("a")).collect()
    assert_frame_equal(out.sort(by="groups"), expected)

    # refer to column via pl.Expr
    out = df.lazy().groupby(pl.col("groups")).agg(pl.mean("a")).collect()
    assert_frame_equal(out.sort(by="groups"), expected)


def test_shift(fruits_cars: pl.DataFrame) -> None:
    df = pl.DataFrame({"a": [1, 2, 3, 4, 5], "b": [1, 2, 3, 4, 5]})
    out = df.select(col("a").shift(1))
    assert_series_equal(out["a"], pl.Series("a", [None, 1, 2, 3, 4]))

    res = fruits_cars.lazy().shift(2).collect()

    expected = pl.DataFrame(
        {
            "A": [None, None, 1, 2, 3],
            "fruits": [None, None, "banana", "banana", "apple"],
            "B": [None, None, 5, 4, 3],
            "cars": [None, None, "beetle", "audi", "beetle"],
        }
    )
    assert_frame_equal(res, expected)

    # negative value
    res = fruits_cars.lazy().shift(-2).collect()
    for rows in [3, 4]:
        for cols in range(4):
            assert res[rows, cols] is None


def test_shift_and_fill() -> None:
    df = pl.DataFrame({"a": [1, 2, 3, 4, 5], "b": [1, 2, 3, 4, 5]})

    # use exprs
    out = df.lazy().with_columns(col("a").shift_and_fill(-2, col("b").mean())).collect()
    assert out["a"].null_count() == 0

    # use df method
    out = df.lazy().shift_and_fill(2, col("b").std()).collect()
    assert out["a"].null_count() == 0


def test_arange() -> None:
    df = pl.DataFrame({"a": [1, 1, 1]}).lazy()
    result = df.filter(pl.col("a") >= pl.arange(0, 3)).collect()
    expected = pl.DataFrame({"a": [1, 1]})
    assert_frame_equal(result, expected)


def test_arg_unique() -> None:
    df = pl.DataFrame({"a": [4, 1, 4]})
    col_a_unique = df.select(col("a").arg_unique())["a"]
    assert_series_equal(col_a_unique, pl.Series("a", [0, 1]).cast(pl.UInt32))


def test_is_unique() -> None:
    df = pl.DataFrame({"a": [4, 1, 4]})
    result = df.select(col("a").is_unique())["a"]
    assert_series_equal(result, pl.Series("a", [False, True, False]))


def test_is_first() -> None:
    df = pl.DataFrame({"a": [4, 1, 4]})
    result = df.select(col("a").is_first())["a"]
    assert_series_equal(result, pl.Series("a", [True, True, False]))

    # struct
    df = pl.DataFrame({"a": [1, 2, 3, 2, None, 2, 1], "b": [0, 2, 3, 2, None, 2, 0]})

    assert df.select(pl.struct(["a", "b"]).is_first()).to_dict(False) == {
        "a": [True, True, True, False, True, False, False]
    }


def test_is_duplicated() -> None:
    df = pl.DataFrame({"a": [4, 1, 4]})
    result = df.select(col("a").is_duplicated())["a"]
    assert_series_equal(result, pl.Series("a", [True, False, True]))


def test_arg_sort() -> None:
    df = pl.DataFrame({"a": [4, 1, 3]})
    assert df.select(col("a").arg_sort())["a"].to_list() == [1, 2, 0]


def test_window_function() -> None:
    ldf = pl.DataFrame(
        {
            "A": [1, 2, 3, 4, 5],
            "fruits": ["banana", "banana", "apple", "apple", "banana"],
            "B": [5, 4, 3, 2, 1],
            "cars": ["beetle", "audi", "beetle", "beetle", "beetle"],
        }
    ).lazy()
    assert ldf.width == 4

    q = ldf.with_columns(
        [
            pl.sum("A").over("fruits").alias("fruit_sum_A"),
            pl.first("B").over("fruits").alias("fruit_first_B"),
            pl.max("B").over("cars").alias("cars_max_B"),
        ]
    )
    assert q.width == 7

    assert q.collect()["cars_max_B"].to_list() == [5, 4, 5, 5, 5]

    out = ldf.select([pl.first("B").over(["fruits", "cars"]).alias("B_first")])
    assert out.collect()["B_first"].to_list() == [5, 4, 3, 3, 5]


def test_when_then_flatten() -> None:
    df = pl.DataFrame({"foo": [1, 2, 3], "bar": [3, 4, 5]})

    assert df.select(
        when(col("foo") > 1)
        .then(col("bar"))
        .when(col("bar") < 3)
        .then(10)
        .otherwise(30)
    )["bar"].to_list() == [30, 4, 5]


def test_describe_plan() -> None:
    assert isinstance(pl.DataFrame({"a": [1]}).lazy().describe_optimized_plan(), str)
    assert isinstance(pl.DataFrame({"a": [1]}).lazy().describe_plan(), str)


def test_inspect(capsys: CaptureFixture[str]) -> None:
    df = pl.DataFrame({"a": [1]})
    df.lazy().inspect().collect()
    captured = capsys.readouterr()
    assert len(captured.out) > 0

    df.select(pl.col("a").cumsum().inspect().alias("bar"))
    res = capsys.readouterr()
    assert len(res.out) > 0


def test_fetch(fruits_cars: pl.DataFrame) -> None:
    res = fruits_cars.lazy().select("*").fetch(2)
    assert_frame_equal(res, res[:2])


def test_window_deadlock() -> None:
    np.random.seed(12)

    df = pl.DataFrame(
        {
            "nrs": [1, 2, 3, None, 5],
            "names": ["foo", "ham", "spam", "egg", None],
            "random": np.random.rand(5),
            "groups": ["A", "A", "B", "C", "B"],
        }
    )

    df = df.select(
        [
            col("*"),  # select all
            col("random").sum().over("groups").alias("sum[random]/groups"),
            col("random").list().over("names").alias("random/name"),
        ]
    )


def test_concat_str() -> None:
    df = pl.DataFrame({"a": ["a", "b", "c"], "b": [1, 2, 3]})

    out = df.select([pl.concat_str(["a", "b"], sep="-")])
    assert out["a"].to_list() == ["a-1", "b-2", "c-3"]

    out = df.select([pl.format("foo_{}_bar_{}", pl.col("a"), "b").alias("fmt")])
    assert out["fmt"].to_list() == ["foo_a_bar_1", "foo_b_bar_2", "foo_c_bar_3"]


def test_fold_filter() -> None:
    df = pl.DataFrame({"a": [1, 2, 3], "b": [0, 1, 2]})

    out = df.filter(
        pl.fold(
            acc=pl.lit(True),
            f=lambda a, b: a & b,
            exprs=[pl.col(c) > 1 for c in df.columns],
        )
    )

    assert out.shape == (1, 2)

    out = df.filter(
        pl.fold(
            acc=pl.lit(True),
            f=lambda a, b: a | b,
            exprs=[pl.col(c) > 1 for c in df.columns],
        )
    )

    assert out.shape == (3, 2)


def test_head_groupby() -> None:
    commodity_prices = {
        "commodity": [
            "Wheat",
            "Wheat",
            "Wheat",
            "Wheat",
            "Corn",
            "Corn",
            "Corn",
            "Corn",
            "Corn",
        ],
        "location": [
            "StPaul",
            "StPaul",
            "StPaul",
            "Chicago",
            "Chicago",
            "Chicago",
            "Chicago",
            "Chicago",
            "Chicago",
        ],
        "seller": [
            "Bob",
            "Charlie",
            "Susan",
            "Paul",
            "Ed",
            "Mary",
            "Paul",
            "Charlie",
            "Norman",
        ],
        "price": [1.0, 0.7, 0.8, 0.55, 2.0, 3.0, 2.4, 1.8, 2.1],
    }
    df = pl.DataFrame(commodity_prices)

    # this query flexes the wildcard exclusion quite a bit.
    keys = ["commodity", "location"]
    out = (
        df.sort(by="price", reverse=True)
        .groupby(keys, maintain_order=True)
        .agg([col("*").exclude(keys).head(2).keep_name()])
        .explode(col("*").exclude(keys))
    )

    assert out.shape == (5, 4)
    assert out.rows() == [
        ("Corn", "Chicago", "Mary", 3.0),
        ("Corn", "Chicago", "Paul", 2.4),
        ("Wheat", "StPaul", "Bob", 1.0),
        ("Wheat", "StPaul", "Susan", 0.8),
        ("Wheat", "Chicago", "Paul", 0.55),
    ]

    df = pl.DataFrame(
        {"letters": ["c", "c", "a", "c", "a", "b"], "nrs": [1, 2, 3, 4, 5, 6]}
    )
    out = df.groupby("letters").tail(2).sort("letters")
    assert_frame_equal(
        out,
        pl.DataFrame({"letters": ["a", "a", "b", "c", "c"], "nrs": [3, 5, 6, 2, 4]}),
    )
    out = df.groupby("letters").head(2).sort("letters")
    assert_frame_equal(
        out,
        pl.DataFrame({"letters": ["a", "a", "b", "c", "c"], "nrs": [3, 5, 6, 1, 2]}),
    )


def test_is_null_is_not_null() -> None:
    df = pl.DataFrame({"nrs": [1, 2, None]})
    assert df.select(col("nrs").is_null())["nrs"].to_list() == [False, False, True]
    assert df.select(col("nrs").is_not_null())["nrs"].to_list() == [True, True, False]


def test_is_nan_is_not_nan() -> None:
    df = pl.DataFrame({"nrs": np.array([1, 2, np.nan])})
    assert df.select(col("nrs").is_nan())["nrs"].to_list() == [False, False, True]
    assert df.select(col("nrs").is_not_nan())["nrs"].to_list() == [True, True, False]


def test_is_finite_is_infinite() -> None:
    df = pl.DataFrame({"nrs": np.array([1, 2, np.inf])})
    assert df.select(col("nrs").is_infinite())["nrs"].to_list() == [False, False, True]
    assert df.select(col("nrs").is_finite())["nrs"].to_list() == [True, True, False]


def test_len() -> None:
    df = pl.DataFrame({"nrs": [1, 2, 3]})
    assert cast(int, df.select(col("nrs").len()).item()) == 3


def test_cum_agg() -> None:
    df = pl.DataFrame({"a": [1, 2, 3, 2]})
    assert_series_equal(
        df.select(pl.col("a").cumsum())["a"], pl.Series("a", [1, 3, 6, 8])
    )
    assert_series_equal(
        df.select(pl.col("a").cummin())["a"], pl.Series("a", [1, 1, 1, 1])
    )
    assert_series_equal(
        df.select(pl.col("a").cummax())["a"], pl.Series("a", [1, 2, 3, 3])
    )
    assert_series_equal(
        df.select(pl.col("a").cumprod())["a"], pl.Series("a", [1, 2, 6, 12])
    )


def test_floor() -> None:
    df = pl.DataFrame({"a": [1.8, 1.2, 3.0]})
    col_a_floor = df.select(pl.col("a").floor())["a"]
    assert_series_equal(col_a_floor, pl.Series("a", [1, 1, 3]).cast(pl.Float64))


def test_round() -> None:
    df = pl.DataFrame({"a": [1.8, 1.2, 3.0]})
    col_a_rounded = df.select(pl.col("a").round(decimals=0))["a"]
    assert_series_equal(col_a_rounded, pl.Series("a", [2, 1, 3]).cast(pl.Float64))


def test_dot() -> None:
    df = pl.DataFrame({"a": [1.8, 1.2, 3.0], "b": [3.2, 1, 2]})
    assert cast(float, df.select(pl.col("a").dot(pl.col("b"))).item()) == 12.96


def test_sort() -> None:
    df = pl.DataFrame({"a": [1, 2, 3, 2]})
    assert_series_equal(
        df.select(pl.col("a").sort())["a"], pl.Series("a", [1, 2, 2, 3])
    )


def test_all_expr() -> None:
    df = pl.DataFrame({"nrs": [1, 2, 3, 4, 5, None]})
    assert_frame_equal(df.select([pl.all()]), df)


def test_any_expr(fruits_cars: pl.DataFrame) -> None:
    assert fruits_cars.with_columns(pl.col("A").cast(bool)).select(pl.any("A"))[0, 0]
    assert fruits_cars.select(pl.any([pl.col("A"), pl.col("B")]))[0, 0]


def test_lazy_columns() -> None:
    df = pl.DataFrame(
        {
            "a": [1],
            "b": [1],
            "c": [1],
        }
    ).lazy()
    assert df.select(["a", "c"]).columns == ["a", "c"]


def test_regex_selection() -> None:
    df = pl.DataFrame(
        {
            "foo": [1],
            "fooey": [1],
            "foobar": [1],
            "bar": [1],
        }
    ).lazy()
    assert df.select([col("^foo.*$")]).columns == ["foo", "fooey", "foobar"]


def test_exclude_selection() -> None:
    df = pl.DataFrame({"a": [1], "b": [1], "c": [True]}).lazy()

    assert df.select([pl.exclude("a")]).columns == ["b", "c"]
    assert df.select(pl.all().exclude(pl.Boolean)).columns == ["a", "b"]
    assert df.select(pl.all().exclude([pl.Boolean])).columns == ["a", "b"]
    assert df.select(pl.all().exclude(NUMERIC_DTYPES)).columns == ["c"]


def test_col_series_selection() -> None:
    df = pl.DataFrame({"a": [1], "b": [1], "c": [1]}).lazy()
    srs = pl.Series(["b", "c"])

    assert df.select(pl.col(srs)).columns == ["b", "c"]


def test_interpolate() -> None:
    df = pl.DataFrame({"a": [1, None, 3]})
    assert df.select(col("a").interpolate())["a"].to_list() == [1, 2, 3]
    assert df["a"].interpolate().to_list() == [1, 2, 3]
    assert df.interpolate()["a"].to_list() == [1, 2, 3]
    assert df.lazy().interpolate().collect()["a"].to_list() == [1, 2, 3]


def test_fill_nan() -> None:
    df = pl.DataFrame({"a": [1.0, np.nan, 3.0]})
    assert_series_equal(df.fill_nan(2.0)["a"], pl.Series("a", [1.0, 2.0, 3.0]))
    assert_series_equal(
        df.lazy().fill_nan(2.0).collect()["a"], pl.Series("a", [1.0, 2.0, 3.0])
    )
    assert_series_equal(
        df.lazy().fill_nan(None).collect()["a"], pl.Series("a", [1.0, None, 3.0])
    )
    assert_series_equal(
        df.select(pl.col("a").fill_nan(2))["a"], pl.Series("a", [1.0, 2.0, 3.0])
    )
    # nearest
    assert pl.Series([None, 1, None, None, None, -8, None, None, 10]).interpolate(
        method="nearest"
    ).to_list() == [None, 1, 1, -8, -8, -8, -8, 10, 10]


def test_fill_null() -> None:
    df = pl.DataFrame({"a": [1.0, None, 3.0]})

    assert df.select([pl.col("a").fill_null(strategy="min")])["a"][1] == 1.0
    assert df.lazy().fill_null(2).collect()["a"].to_list() == [1.0, 2.0, 3.0]

    with pytest.raises(ValueError, match="must specify either"):
        df.fill_null()
    with pytest.raises(ValueError, match="cannot specify both"):
        df.fill_null(value=3.0, strategy="max")
    with pytest.raises(ValueError, match="can only specify 'limit'"):
        df.fill_null(strategy="max", limit=2)


def test_backward_fill() -> None:
    df = pl.DataFrame({"a": [1.0, None, 3.0]})
    col_a_backward_fill = df.select([pl.col("a").backward_fill()])["a"]
    assert_series_equal(col_a_backward_fill, pl.Series("a", [1, 3, 3]).cast(pl.Float64))


def test_take(fruits_cars: pl.DataFrame) -> None:
    df = fruits_cars

    # out of bounds error
    with pytest.raises(pl.ComputeError):
        (
            df.sort("fruits").select(
                [col("B").reverse().take([1, 2]).list().over("fruits"), "fruits"]
            )
        )

    for index in [[0, 1], pl.Series([0, 1]), np.array([0, 1])]:
        out = df.sort("fruits").select(
            [
                col("B")
                .reverse()
                .take(index)  # type: ignore[arg-type]
                .list()
                .over("fruits"),
                "fruits",
            ]
        )

        assert out[0, "B"].to_list() == [2, 3]
        assert out[4, "B"].to_list() == [1, 4]

    out = df.sort("fruits").select(
        [col("B").reverse().take(pl.lit(1)).list().over("fruits"), "fruits"]
    )
    assert out[0, "B"] == 3
    assert out[4, "B"] == 4


def test_select_by_col_list(fruits_cars: pl.DataFrame) -> None:
    df = fruits_cars
    result = df.select(col(["A", "B"]).sum())
    expected = pl.DataFrame({"A": 15, "B": 15})
    assert_frame_equal(result, expected)


def test_select_args_kwargs() -> None:
    df = pl.DataFrame({"foo": [1, 2], "bar": [3, 4], "ham": ["a", "b"]})

    # Single column name
    result = df.select("foo")
    expected = pl.DataFrame({"foo": [1, 2]})
    assert_frame_equal(result, expected)

    # Column names as list
    result = df.select(["foo", "bar"])
    expected = pl.DataFrame({"foo": [1, 2], "bar": [3, 4]})
    assert_frame_equal(result, expected)

    # Column names as positional arguments
    result = df.select("foo", "bar", "ham")
    expected = df
    assert_frame_equal(result, expected)

    # Keyword arguments
    result = df.select(oof="foo")
    expected = pl.DataFrame({"oof": [1, 2]})
    assert_frame_equal(result, expected)

    # Mixed
    result = df.select(["bar"], "foo", oof="foo")
    expected = pl.DataFrame({"bar": [3, 4], "foo": [1, 2], "oof": [1, 2]})
    assert_frame_equal(result, expected)


def test_rolling(fruits_cars: pl.DataFrame) -> None:
    df = fruits_cars
    out = df.select(
        [
            pl.col("A").rolling_min(3, min_periods=1).alias("1"),
            pl.col("A").rolling_min(3).alias("1b"),
            pl.col("A").rolling_mean(3, min_periods=1).alias("2"),
            pl.col("A").rolling_mean(3).alias("2b"),
            pl.col("A").rolling_max(3, min_periods=1).alias("3"),
            pl.col("A").rolling_max(3).alias("3b"),
            pl.col("A").rolling_sum(3, min_periods=1).alias("4"),
            pl.col("A").rolling_sum(3).alias("4b"),
            # below we use .round purely for the ability to do assert frame equality
            pl.col("A").rolling_std(3).round(1).alias("std"),
            pl.col("A").rolling_var(3).round(1).alias("var"),
        ]
    )

    assert_frame_equal(
        out,
        pl.DataFrame(
            {
                "1": [1, 1, 1, 2, 3],
                "1b": [None, None, 1, 2, 3],
                "2": [1.0, 1.5, 2.0, 3.0, 4.0],
                "2b": [None, None, 2.0, 3.0, 4.0],
                "3": [1, 2, 3, 4, 5],
                "3b": [None, None, 3, 4, 5],
                "4": [1, 3, 6, 9, 12],
                "4b": [None, None, 6, 9, 12],
                "std": [None, None, 1.0, 1.0, 1.0],
                "var": [None, None, 1.0, 1.0, 1.0],
            }
        ),
    )

    out_single_val_variance = df.select(
        [
            pl.col("A").rolling_std(3, min_periods=1).round(decimals=4).alias("std"),
            pl.col("A").rolling_var(3, min_periods=1).round(decimals=1).alias("var"),
        ]
    )

    assert cast(float, out_single_val_variance[0, "std"]) == 0.0
    assert cast(float, out_single_val_variance[0, "var"]) == 0.0


def test_rolling_apply() -> None:
    s = pl.Series("A", [1.0, 2.0, 9.0, 2.0, 13.0], dtype=pl.Float64)
    out = s.rolling_apply(function=lambda s: s.std(), window_size=3)
    assert out[0] is None
    assert out[1] is None
    assert out[2] == 4.358898943540674
    assert out.dtype is pl.Float64

    s = pl.Series("A", [1.0, 2.0, 9.0, 2.0, 13.0], dtype=pl.Float32)
    out = s.rolling_apply(function=lambda s: s.std(), window_size=3)
    assert out[0] is None
    assert out[1] is None
    assert out[2] == 4.358899116516113
    assert out.dtype is pl.Float32

    s = pl.Series("A", [1, 2, 9, 2, 13], dtype=pl.Int32)
    out = s.rolling_apply(function=lambda s: s.sum(), window_size=3)
    assert out[0] is None
    assert out[1] is None
    assert out[2] == 12
    assert out.dtype is pl.Int32

    s = pl.Series("A", [1.0, 2.0, 9.0, 2.0, 13.0], dtype=pl.Float64)
    out = s.rolling_apply(
        function=lambda s: s.std(), window_size=3, weights=[1.0, 2.0, 3.0]
    )
    assert out[0] is None
    assert out[1] is None
    assert out[2] == 14.224392195567912
    assert out.dtype is pl.Float64

    s = pl.Series("A", [1.0, 2.0, 9.0, 2.0, 13.0], dtype=pl.Float32)
    out = s.rolling_apply(
        function=lambda s: s.std(), window_size=3, weights=[1.0, 2.0, 3.0]
    )
    assert out[0] is None
    assert out[1] is None
    assert out[2] == 14.22439193725586
    assert out.dtype is pl.Float32

    s = pl.Series("A", [1, 2, 9, None, 13], dtype=pl.Int32)
    out = s.rolling_apply(
        function=lambda s: s.sum(), window_size=3, weights=[1.0, 2.0, 3.0]
    )
    assert out[0] is None
    assert out[1] is None
    assert out[2] == 32.0
    assert out.dtype is pl.Float64
    s = pl.Series("A", [1, 2, 9, 2, 10])

    # compare rolling_apply to specific rolling functions
    s = pl.Series("A", list(range(5)), dtype=pl.Float64)
    roll_app_sum = s.rolling_apply(
        function=lambda s: s.sum(),
        window_size=3,
        weights=[1.0, 2.1, 3.2],
        min_periods=2,
        center=True,
    )

    roll_sum = s.rolling_sum(
        window_size=3, weights=[1.0, 2.1, 3.2], min_periods=2, center=True
    )

    assert (roll_app_sum - roll_sum).abs().sum() < 0.0001

    s = pl.Series("A", list(range(6)), dtype=pl.Float64)
    roll_app_std = s.rolling_apply(
        function=lambda s: s.std(),
        window_size=4,
        weights=[1.0, 2.0, 3.0, 0.1],
        min_periods=3,
        center=False,
    )

    roll_std = s.rolling_std(
        window_size=4, weights=[1.0, 2.0, 3.0, 0.1], min_periods=3, center=False
    )

    assert (roll_app_std - roll_std).abs().sum() < 0.0001


def test_arr_namespace(fruits_cars: pl.DataFrame) -> None:
    df = fruits_cars
    out = df.select(
        [
            "fruits",
            pl.col("B").list().over("fruits").arr.min().alias("B_by_fruits_min1"),
            pl.col("B").min().list().over("fruits").alias("B_by_fruits_min2"),
            pl.col("B").list().over("fruits").arr.max().alias("B_by_fruits_max1"),
            pl.col("B").max().list().over("fruits").alias("B_by_fruits_max2"),
            pl.col("B").list().over("fruits").arr.sum().alias("B_by_fruits_sum1"),
            pl.col("B").sum().list().over("fruits").alias("B_by_fruits_sum2"),
            pl.col("B").list().over("fruits").arr.mean().alias("B_by_fruits_mean1"),
            pl.col("B").mean().list().over("fruits").alias("B_by_fruits_mean2"),
        ]
    )
    expected = pl.DataFrame(
        {
            "fruits": ["banana", "banana", "apple", "apple", "banana"],
            "B_by_fruits_min1": [1, 1, 2, 2, 1],
            "B_by_fruits_min2": [1, 1, 2, 2, 1],
            "B_by_fruits_max1": [5, 5, 3, 3, 5],
            "B_by_fruits_max2": [5, 5, 3, 3, 5],
            "B_by_fruits_sum1": [10, 10, 5, 5, 10],
            "B_by_fruits_sum2": [10, 10, 5, 5, 10],
            "B_by_fruits_mean1": [
                3.3333333333333335,
                3.3333333333333335,
                2.5,
                2.5,
                3.3333333333333335,
            ],
            "B_by_fruits_mean2": [
                3.3333333333333335,
                3.3333333333333335,
                2.5,
                2.5,
                3.3333333333333335,
            ],
        }
    )
    assert_frame_equal(out, expected)


def test_arithmetic() -> None:
    df = pl.DataFrame({"a": [1, 2, 3]})

    out = df.select(
        [
            (col("a") % 2).alias("1"),
            (2 % col("a")).alias("2"),
            (1 // col("a")).alias("3"),
            (1 * col("a")).alias("4"),
            (1 + col("a")).alias("5"),
            (1 - col("a")).alias("6"),
            (col("a") // 2).alias("7"),
            (col("a") * 2).alias("8"),
            (col("a") + 2).alias("9"),
            (col("a") - 2).alias("10"),
            (-col("a")).alias("11"),
        ]
    )
    expected = pl.DataFrame(
        {
            "1": [1, 0, 1],
            "2": [0, 0, 2],
            "3": [1, 0, 0],
            "4": [1, 2, 3],
            "5": [2, 3, 4],
            "6": [0, -1, -2],
            "7": [0, 1, 1],
            "8": [2, 4, 6],
            "9": [3, 4, 5],
            "10": [-1, 0, 1],
            "11": [-1, -2, -3],
        }
    )
    assert_frame_equal(out, expected)

    # floating point floor divide
    x = 10.4
    step = 0.5
    df = pl.DataFrame({"x": [x]})
    assert df.with_columns(pl.col("x") // step)[0, 0] == x // step


def test_ufunc() -> None:
    # NOTE: unfortunately we must use cast instead of a type: ignore comment
    #   1. CI job with Python 3.10, numpy==1.23.1 -> mypy complains about arg-type
    #   2. so we try to resolve it with type: ignore[arg-type]
    #   3. CI job with Python 3.7, numpy==1.21.6 -> mypy complains about
    #       unused type: ignore comment
    # for more information, see: https://github.com/python/mypy/issues/8823
    df = pl.DataFrame([pl.Series("a", [1, 2, 3, 4], dtype=pl.UInt8)])
    out = df.select(
        [
            np.power(cast(Any, pl.col("a")), 2).alias("power_uint8"),
            np.power(cast(Any, pl.col("a")), 2.0).alias("power_float64"),
            np.power(cast(Any, pl.col("a")), 2, dtype=np.uint16).alias("power_uint16"),
        ]
    )
    expected = pl.DataFrame(
        [
            pl.Series("power_uint8", [1, 4, 9, 16], dtype=pl.UInt8),
            pl.Series("power_float64", [1.0, 4.0, 9.0, 16.0], dtype=pl.Float64),
            pl.Series("power_uint16", [1, 4, 9, 16], dtype=pl.UInt16),
        ]
    )
    assert_frame_equal(out, expected)
    assert out.dtypes == expected.dtypes


def test_ufunc_expr_not_first() -> None:
    """Check numpy ufunc expressions also work if expression not the first argument."""
    df = pl.DataFrame([pl.Series("a", [1, 2, 3], dtype=pl.Float64)])
    out = df.select(
        [
            np.power(2.0, cast(Any, pl.col("a"))).alias("power"),
            (2.0 / cast(Any, pl.col("a"))).alias("divide_scalar"),
            (np.array([2, 2, 2]) / cast(Any, pl.col("a"))).alias("divide_array"),
        ]
    )
    expected = pl.DataFrame(
        [
            pl.Series("power", [2**1, 2**2, 2**3], dtype=pl.Float64),
            pl.Series("divide_scalar", [2 / 1, 2 / 2, 2 / 3], dtype=pl.Float64),
            pl.Series("divide_array", [2 / 1, 2 / 2, 2 / 3], dtype=pl.Float64),
        ]
    )
    assert_frame_equal(out, expected)


def test_ufunc_multiple_expr() -> None:
    df = pl.DataFrame(
        [
            pl.Series("a", [1, 2, 3], dtype=pl.Float64),
            pl.Series("b", [4, 5, 6], dtype=pl.Float64),
        ]
    )

    with pytest.raises(
        ValueError, match="Numpy ufunc can only be used with one expression, 2 given"
    ):
        df.select(np.arctan2(pl.col("a"), pl.col("b")))  # type: ignore[call-overload]


def test_clip() -> None:
    df = pl.DataFrame({"a": [1, 2, 3, 4, 5]})
    assert df.select(pl.col("a").clip(2, 4))["a"].to_list() == [2, 2, 3, 4, 4]
    assert pl.Series([1, 2, 3, 4, 5]).clip(2, 4).to_list() == [2, 2, 3, 4, 4]
    assert pl.Series([1, 2, 3, 4, 5]).clip_min(3).to_list() == [3, 3, 3, 4, 5]
    assert pl.Series([1, 2, 3, 4, 5]).clip_max(3).to_list() == [1, 2, 3, 3, 3]


def test_argminmax() -> None:
    df = pl.DataFrame({"a": [1, 2, 3, 4, 5], "b": [1, 1, 2, 2, 2]})
    out = df.select(
        [
            pl.col("a").arg_min().alias("min"),
            pl.col("a").arg_max().alias("max"),
        ]
    )
    assert out["max"][0] == 4
    assert out["min"][0] == 0

    out = df.groupby("b", maintain_order=True).agg(
        [pl.col("a").arg_min().alias("min"), pl.col("a").arg_max().alias("max")]
    )
    assert out["max"][0] == 1
    assert out["min"][0] == 0


def test_expr_bool_cmp() -> None:
    # Since expressions are lazy they should not be evaluated as
    # bool(x), this has the nice side effect of throwing an error
    # if someone tries to chain them via the and|or operators
    df = pl.DataFrame({"a": [1, 2, 3, 4, 5], "b": [1, 2, 3, 4, 5]})

    with pytest.raises(ValueError):
        df.select([(pl.col("a") > pl.col("b")) and (pl.col("b") > pl.col("b"))])

    with pytest.raises(ValueError):
        df.select([(pl.col("a") > pl.col("b")) or (pl.col("b") > pl.col("b"))])


def test_is_in() -> None:
    df = pl.DataFrame({"a": [1, 2, 3]})
    assert df.select(pl.col("a").is_in([1, 2]))["a"].to_list() == [
        True,
        True,
        False,
    ]


def test_rename() -> None:
    lf = pl.DataFrame({"a": [1], "b": [2], "c": [3]}).lazy()
    out = lf.rename({"a": "foo", "b": "bar"}).collect()
    assert out.columns == ["foo", "bar", "c"]


def test_with_column_renamed(fruits_cars: pl.DataFrame) -> None:
    res = fruits_cars.lazy().rename({"A": "C"}).collect()
    assert res.columns[0] == "C"


def test_with_columns_single_series() -> None:
    df = pl.DataFrame({"a": [1, 2]})
    result = df.lazy().with_columns(pl.Series("b", [3, 4])).collect()

    expected = pl.DataFrame({"a": [1, 2], "b": [3, 4]})
    assert_frame_equal(result, expected)


def test_reverse() -> None:
    out = pl.DataFrame({"a": [1, 2], "b": [3, 4]}).lazy().reverse()
    expected = pl.DataFrame({"a": [2, 1], "b": [4, 3]})
    assert_frame_equal(out.collect(), expected)


def test_limit(fruits_cars: pl.DataFrame) -> None:
    assert_frame_equal(fruits_cars.lazy().limit(1).collect(), fruits_cars[0, :])


def test_head(fruits_cars: pl.DataFrame) -> None:
    assert_frame_equal(fruits_cars.lazy().head(2).collect(), fruits_cars[:2, :])


def test_tail(fruits_cars: pl.DataFrame) -> None:
    assert_frame_equal(fruits_cars.lazy().tail(2).collect(), fruits_cars[3:, :])


def test_last(fruits_cars: pl.DataFrame) -> None:
    result = fruits_cars.lazy().last().collect()
    expected = fruits_cars[(len(fruits_cars) - 1) :, :]
    assert_frame_equal(result, expected)


def test_first(fruits_cars: pl.DataFrame) -> None:
    assert_frame_equal(fruits_cars.lazy().first().collect(), fruits_cars[0, :])


def test_join_suffix() -> None:
    df_left = pl.DataFrame(
        {
            "a": ["a", "b", "a", "z"],
            "b": [1, 2, 3, 4],
            "c": [6, 5, 4, 3],
        }
    )
    df_right = pl.DataFrame(
        {
            "a": ["b", "c", "b", "a"],
            "b": [0, 3, 9, 6],
            "c": [1, 0, 2, 1],
        }
    )
    out = df_left.join(df_right, on="a", suffix="_bar")
    assert out.columns == ["a", "b", "c", "b_bar", "c_bar"]
    out = df_left.lazy().join(df_right.lazy(), on="a", suffix="_bar").collect()
    assert out.columns == ["a", "b", "c", "b_bar", "c_bar"]


@pytest.mark.parametrize("no_optimization", [False, True])
def test_collect_all(df: pl.DataFrame, no_optimization: bool) -> None:
    lf1 = df.lazy().select(pl.col("int").sum())
    lf2 = df.lazy().select((pl.col("floats") * 2).sum())
    out = pl.collect_all([lf1, lf2], no_optimization=no_optimization)
    assert cast(int, out[0].item()) == 6
    assert cast(float, out[1].item()) == 12.0


def test_spearman_corr() -> None:
    df = pl.DataFrame(
        {
            "era": [1, 1, 1, 2, 2, 2],
            "prediction": [2, 4, 5, 190, 1, 4],
            "target": [1, 3, 2, 1, 43, 3],
        }
    )

    out = (
        df.groupby("era", maintain_order=True).agg(
            pl.spearman_rank_corr(pl.col("prediction"), pl.col("target")).alias("c"),
        )
    )["c"]
    assert np.isclose(out[0], 0.5)
    assert np.isclose(out[1], -1.0)

    # we can also pass in column names directly
    out = (
        df.groupby("era", maintain_order=True).agg(
            pl.spearman_rank_corr("prediction", "target").alias("c"),
        )
    )["c"]
    assert np.isclose(out[0], 0.5)
    assert np.isclose(out[1], -1.0)


def test_pearson_corr() -> None:
    df = pl.DataFrame(
        {
            "era": [1, 1, 1, 2, 2, 2],
            "prediction": [2, 4, 5, 190, 1, 4],
            "target": [1, 3, 2, 1, 43, 3],
        }
    )

    out = (
        df.groupby("era", maintain_order=True).agg(
            pl.pearson_corr(pl.col("prediction"), pl.col("target")).alias("c"),
        )
    )["c"]
    assert out.to_list() == pytest.approx([0.6546536707079772, -5.477514993831792e-1])

    # we can also pass in column names directly
    out = (
        df.groupby("era", maintain_order=True).agg(
            pl.pearson_corr("prediction", "target").alias("c"),
        )
    )["c"]
    assert out.to_list() == pytest.approx([0.6546536707079772, -5.477514993831792e-1])


def test_cov(fruits_cars: pl.DataFrame) -> None:
    assert cast(float, fruits_cars.select(pl.cov("A", "B")).item()) == -2.5
    assert (
        cast(float, fruits_cars.select(pl.cov(pl.col("A"), pl.col("B"))).item()) == -2.5
    )


def test_std(fruits_cars: pl.DataFrame) -> None:
    assert fruits_cars.lazy().std().collect()["A"][0] == pytest.approx(
        1.5811388300841898
    )


def test_var(fruits_cars: pl.DataFrame) -> None:
    assert fruits_cars.lazy().var().collect()["A"][0] == pytest.approx(2.5)


def test_max(fruits_cars: pl.DataFrame) -> None:
    assert fruits_cars.lazy().max().collect()["A"][0] == 5
    assert fruits_cars.select(pl.col("A").max())["A"][0] == 5


def test_min(fruits_cars: pl.DataFrame) -> None:
    assert fruits_cars.lazy().min().collect()["A"][0] == 1
    assert fruits_cars.select(pl.col("A").min())["A"][0] == 1


def test_median(fruits_cars: pl.DataFrame) -> None:
    assert fruits_cars.lazy().median().collect()["A"][0] == 3
    assert fruits_cars.select(pl.col("A").median())["A"][0] == 3


def test_quantile(fruits_cars: pl.DataFrame) -> None:
    assert fruits_cars.lazy().quantile(0.25, "nearest").collect()["A"][0] == 2
    assert fruits_cars.select(pl.col("A").quantile(0.25, "nearest"))["A"][0] == 2

    assert fruits_cars.lazy().quantile(0.24, "lower").collect()["A"][0] == 1
    assert fruits_cars.select(pl.col("A").quantile(0.24, "lower"))["A"][0] == 1

    assert fruits_cars.lazy().quantile(0.26, "higher").collect()["A"][0] == 3
    assert fruits_cars.select(pl.col("A").quantile(0.26, "higher"))["A"][0] == 3

    assert fruits_cars.lazy().quantile(0.24, "midpoint").collect()["A"][0] == 1.5
    assert fruits_cars.select(pl.col("A").quantile(0.24, "midpoint"))["A"][0] == 1.5

    assert fruits_cars.lazy().quantile(0.24, "linear").collect()["A"][0] == 1.96
    assert fruits_cars.select(pl.col("A").quantile(0.24, "linear"))["A"][0] == 1.96


def test_is_between(fruits_cars: pl.DataFrame) -> None:
    result = fruits_cars.select(pl.col("A").is_between(2, 4)).to_series()
    assert_series_equal(result, pl.Series("A", [False, True, True, True, False]))

    result = fruits_cars.select(pl.col("A").is_between(2, 4, closed="none")).to_series()
    assert_series_equal(result, pl.Series("A", [False, False, True, False, False]))

    result = fruits_cars.select(pl.col("A").is_between(2, 4, closed="both")).to_series()
    assert_series_equal(result, pl.Series("A", [False, True, True, True, False]))

    result = fruits_cars.select(
        pl.col("A").is_between(2, 4, closed="right")
    ).to_series()
    assert_series_equal(result, pl.Series("A", [False, False, True, True, False]))

    result = fruits_cars.select(pl.col("A").is_between(2, 4, closed="left")).to_series()
    assert_series_equal(result, pl.Series("A", [False, True, True, False, False]))


def test_is_between_data_types() -> None:
    df = pl.DataFrame(
        {
            "flt": [1.4, 1.2, 2.5],
            "int": [2, 3, 4],
            "str": ["xyz", "str", "abc"],
            "date": [date(2020, 1, 1), date(2020, 2, 2), date(2020, 3, 3)],
            "datetime": [
                datetime(2020, 1, 1, 0, 0, 0),
                datetime(2020, 1, 1, 10, 0, 0),
                datetime(2020, 1, 1, 12, 0, 0),
            ],
            "tm": [time(10, 30), time(0, 45), time(15, 15)],
        }
    )

    # on purpose, for float and int, we pass in a mixture of bound data types
    assert_series_equal(
        df.select(pl.col("flt").is_between(1, 2.3))[:, 0],
        pl.Series("flt", [True, True, False]),
    )
    assert_series_equal(
        df.select(pl.col("int").is_between(1.5, 3))[:, 0],
        pl.Series("int", [True, True, False]),
    )
    assert_series_equal(
        df.select(pl.col("date").is_between(date(2019, 1, 1), date(2020, 2, 5)))[:, 0],
        pl.Series("date", [True, True, False]),
    )
    assert_series_equal(
        df.select(
            pl.col("datetime").is_between(
                datetime(2020, 1, 1, 5, 0, 0), datetime(2020, 1, 1, 11, 0, 0)
            )
        )[:, 0],
        pl.Series("datetime", [False, True, False]),
    )
    assert_series_equal(
        df.select(
            pl.col("str").is_between(pl.lit("str"), pl.lit("zzz"), closed="left")
        )[:, 0],
        pl.Series("str", [True, True, False]),
    )
    assert_series_equal(
        df.select(
            pl.col("tm")
            .is_between(time(0, 45), time(10, 30), closed="right")
            .alias("tm_between")
        )[:, 0],
        pl.Series("tm_between", [True, False, False]),
    )


def test_unique() -> None:
    df = pl.DataFrame({"a": [1, 2, 2], "b": [3, 3, 3]})

    expected = pl.DataFrame({"a": [1, 2], "b": [3, 3]})
    assert_frame_equal(df.lazy().unique(maintain_order=True).collect(), expected)

    result = df.lazy().unique(subset="b", maintain_order=True).collect()
    expected = pl.DataFrame({"a": [1], "b": [3]})
    assert_frame_equal(result, expected)

    s0 = pl.Series("a", [1, 2, None, 2])
    # test if the null is included
    assert s0.unique().to_list() == [None, 1, 2]


def test_unique_on_sorted() -> None:
    assert (
        pl.DataFrame({"a": [1, 1, 3], "b": [1, 2, 3]})
        .with_columns([pl.col("a").set_sorted()])
        .unique(subset="a", keep="last")
    ).to_dict(False) == {"a": [1, 3], "b": [2, 3]}


def test_lazy_concat(df: pl.DataFrame) -> None:
    shape = df.shape
    shape = (shape[0] * 2, shape[1])

    out = pl.concat([df.lazy(), df.lazy()]).collect()
    assert out.shape == shape
    assert_frame_equal(out, df.vstack(df.clone()))


def test_max_min_multiple_columns(fruits_cars: pl.DataFrame) -> None:
    res = fruits_cars.select(pl.max(["A", "B"]).alias("max"))
    assert_series_equal(res.to_series(0), pl.Series("max", [5, 4, 3, 4, 5]))

    res = fruits_cars.select(pl.min(["A", "B"]).alias("min"))
    assert_series_equal(res.to_series(0), pl.Series("min", [1, 2, 3, 2, 1]))


def test_max_min_wildcard_columns(fruits_cars: pl.DataFrame) -> None:
    res = fruits_cars.select([pl.col(pl.datatypes.Int64)]).select(pl.min(["*"]))
    assert_series_equal(res.to_series(0), pl.Series("min", [1, 2, 3, 2, 1]))
    res = fruits_cars.select([pl.col(pl.datatypes.Int64)]).select(pl.min([pl.all()]))
    assert_series_equal(res.to_series(0), pl.Series("min", [1, 2, 3, 2, 1]))

    res = fruits_cars.select([pl.col(pl.datatypes.Int64)]).select(pl.max(["*"]))
    assert_series_equal(res.to_series(0), pl.Series("max", [5, 4, 3, 4, 5]))
    res = fruits_cars.select([pl.col(pl.datatypes.Int64)]).select(pl.max([pl.all()]))
    assert_series_equal(res.to_series(0), pl.Series("max", [5, 4, 3, 4, 5]))

    res = fruits_cars.select([pl.col(pl.datatypes.Int64)]).select(
        pl.max([pl.all(), "A", "*"])
    )
    assert_series_equal(res.to_series(0), pl.Series("max", [5, 4, 3, 4, 5]))


def test_head_tail(fruits_cars: pl.DataFrame) -> None:
    res_expr = fruits_cars.select([pl.head("A", 2)])
    res_series = pl.head(fruits_cars["A"], 2)
    expected = pl.Series("A", [1, 2])
    assert_series_equal(res_expr.to_series(0), expected)
    assert_series_equal(res_series, expected)

    res_expr = fruits_cars.select([pl.tail("A", 2)])
    res_series = pl.tail(fruits_cars["A"], 2)
    expected = pl.Series("A", [4, 5])
    assert_series_equal(res_expr.to_series(0), expected)
    assert_series_equal(res_series, expected)


def test_lower_bound_upper_bound(fruits_cars: pl.DataFrame) -> None:
    res_expr = fruits_cars.select(pl.col("A").lower_bound())
    assert res_expr["A"][0] < -10_000_000
    res_expr = fruits_cars.select(pl.col("A").upper_bound())
    assert res_expr["A"][0] > 10_000_000


def test_nested_min_max() -> None:
    df = pl.DataFrame({"a": [1], "b": [2], "c": [3], "d": [4]})
    out = df.with_columns(pl.max([pl.min(["a", "b"]), pl.min(["c", "d"])]).alias("t"))
    assert out.shape == (1, 5)
    assert out.row(0) == (1, 2, 3, 4, 3)
    assert out.columns == ["a", "b", "c", "d", "t"]


def test_self_join() -> None:
    # 2720
    df = pl.from_dict(
        data={
            "employee_id": [100, 101, 102],
            "employee_name": ["James", "Alice", "Bob"],
            "manager_id": [None, 100, 101],
        }
    ).lazy()

    out = (
        df.join(other=df, left_on="manager_id", right_on="employee_id", how="left")
        .select(
            exprs=[
                pl.col("employee_id"),
                pl.col("employee_name"),
                pl.col("employee_name_right").alias("manager_name"),
            ]
        )
        .fetch()
    )
    assert set(out.rows()) == {
        (100, "James", None),
        (101, "Alice", "James"),
        (102, "Bob", "Alice"),
    }


def test_preservation_of_subclasses() -> None:
    """Test for LazyFrame inheritance."""

    # We should be able to inherit from polars.LazyFrame
    class SubClassedLazyFrame(pl.LazyFrame):
        pass

    # The constructor creates an object which is an instance of both the
    # superclass and subclass
    ldf = pl.DataFrame({"column_1": [1, 2, 3]}).lazy()
    ldf.__class__ = SubClassedLazyFrame
    extended_ldf = ldf.with_columns(pl.lit(1).alias("column_2"))
    assert isinstance(extended_ldf, pl.LazyFrame)
    assert isinstance(extended_ldf, SubClassedLazyFrame)


def test_group_lengths() -> None:
    df = pl.DataFrame(
        {
            "group": ["A", "A", "A", "B", "B", "B", "B"],
            "id": ["1", "1", "2", "3", "4", "3", "5"],
        }
    )

    result = df.groupby(["group"], maintain_order=True).agg(
        [
            (pl.col("id").unique_counts() / pl.col("id").len())
            .sum()
            .alias("unique_counts_sum"),
            pl.col("id").unique().len().alias("unique_len"),
        ]
    )
    expected = pl.DataFrame(
        {
            "group": ["A", "B"],
            "unique_counts_sum": [1.0, 1.0],
            "unique_len": [2, 3],
        },
        schema_overrides={"unique_len": pl.UInt32},
    )
    assert_frame_equal(result, expected)


def test_quantile_filtered_agg() -> None:
    assert (
        pl.DataFrame(
            {
                "group": [0, 0, 0, 0, 1, 1, 1, 1],
                "value": [1, 2, 3, 4, 1, 2, 3, 4],
            }
        )
        .groupby("group")
        .agg(pl.col("value").filter(pl.col("value") < 2).quantile(0.5))["value"]
        .to_list()
    ) == [1.0, 1.0]


def test_lazy_schema() -> None:
    lf = pl.DataFrame(
        {
            "foo": [1, 2, 3],
            "bar": [6.0, 7.0, 8.0],
            "ham": ["a", "b", "c"],
        }
    ).lazy()
    assert lf.schema == {"foo": pl.Int64, "bar": pl.Float64, "ham": pl.Utf8}

    lf = pl.DataFrame(
        {
            "foo": [1, 2, 3],
            "bar": [6.0, 7.0, 8.0],
            "ham": ["a", "b", "c"],
        }
    ).lazy()
    assert lf.dtypes == [pl.Int64, pl.Float64, pl.Utf8]

    lfe = lf.cleared()
    assert lfe.schema == lf.schema


def test_deadlocks_3409() -> None:
    assert (
        pl.DataFrame(
            {
                "col1": [[1, 2, 3]],
            }
        )
        .with_columns([pl.col("col1").arr.eval(pl.element().apply(lambda x: x))])
        .to_dict(False)
    ) == {"col1": [[1, 2, 3]]}

    assert (
        pl.DataFrame(
            {
                "col1": [1, 2, 3],
            }
        )
        .with_columns([pl.col("col1").cumulative_eval(pl.element().map(lambda x: 0))])
        .to_dict(False)
    ) == {"col1": [0, 0, 0]}


def test_predicate_count_vstack() -> None:
    l1 = pl.DataFrame(
        {
            "k": ["x", "y"],
            "v": [3, 2],
        }
    ).lazy()
    l2 = pl.DataFrame(
        {
            "k": ["x", "y"],
            "v": [5, 7],
        }
    ).lazy()
    assert pl.concat([l1, l2]).filter(pl.count().over("k") == 2).collect()[
        "v"
    ].to_list() == [3, 2, 5, 7]


def test_lazy_method() -> None:
    # We want to support `.lazy()` on a Lazy DataFrame as to allow more generic user
    # code.
    df = pl.DataFrame({"a": [1, 1, 2, 2, 3, 3], "b": [1, 2, 3, 4, 5, 6]})
    lazy_df = df.lazy()

    assert lazy_df.lazy() == lazy_df


def test_update_schema_after_projection_pd_t4157() -> None:
    assert pl.DataFrame({"c0": [], "c1": [], "c2": []}).lazy().rename(
        {
            "c2": "c2_",
        }
    ).drop("c2_").select(pl.col("c0")).collect().columns == ["c0"]


def test_type_coercion_unknown_4190() -> None:
    df = (
        pl.DataFrame({"a": [1, 2, 3], "b": [1, 2, 3]})
        .lazy()
        .with_columns([pl.col("a") & pl.col("a").fill_null(True)])
    ).collect()
    assert df.shape == (3, 2)
    assert df.rows() == [(1, 1), (2, 2), (3, 3)]


def test_all_any_accept_expr() -> None:
    df = pl.DataFrame(
        {
            "a": [1, None, 2],
            "b": [1, 2, None],
        }
    )
    assert df.select(
        [
            pl.any(pl.all().is_null()).alias("null_in_row"),
            pl.all(pl.all().is_null()).alias("all_null_in_row"),
        ]
    ).to_dict(False) == {
        "null_in_row": [False, True, True],
        "all_null_in_row": [False, False, False],
    }


def test_lazy_cache_same_key() -> None:
    df = pl.DataFrame({"a": [1, 2, 3], "b": [3, 4, 5], "c": ["x", "y", "z"]}).lazy()

    # these have the same schema, but should not be used by cache as they are different
    add_node = df.select([(pl.col("a") + pl.col("b")).alias("a"), pl.col("c")]).cache()
    mult_node = df.select([(pl.col("a") * pl.col("b")).alias("a"), pl.col("c")]).cache()

    assert mult_node.join(add_node, on="c", suffix="_mult").select(
        [(pl.col("a") - pl.col("a_mult")).alias("a"), pl.col("c")]
    ).collect().to_dict(False) == {"a": [-1, 2, 7], "c": ["x", "y", "z"]}


def test_lazy_cache_hit(monkeypatch: Any, capfd: Any) -> None:
    monkeypatch.setenv("POLARS_VERBOSE", "1")

    df = pl.DataFrame({"a": [1, 2, 3], "b": [3, 4, 5], "c": ["x", "y", "z"]}).lazy()
    add_node = df.select([(pl.col("a") + pl.col("b")).alias("a"), pl.col("c")]).cache()
    assert add_node.join(add_node, on="c", suffix="_mult").select(
        [(pl.col("a") - pl.col("a_mult")).alias("a"), pl.col("c")]
    ).collect().to_dict(False) == {"a": [0, 0, 0], "c": ["x", "y", "z"]}

    (out, _) = capfd.readouterr()
    assert "CACHE HIT" in out


def test_quadratic_behavior_4736() -> None:
    # no assert; if this function does not stall our tests it has passed!
    df = pl.DataFrame(schema=list(ascii_letters))
    df.lazy().select(reduce(add, (pl.col(fld) for fld in df.columns)))


@pytest.mark.parametrize("input_dtype", [pl.Utf8, pl.Int64, pl.Float64])
def test_from_epoch(input_dtype: PolarsDataType) -> None:
    ldf = pl.DataFrame(
        [
            pl.Series("timestamp_d", [13285]).cast(input_dtype),
            pl.Series("timestamp_s", [1147880044]).cast(input_dtype),
            pl.Series("timestamp_ms", [1147880044 * 1_000]).cast(input_dtype),
            pl.Series("timestamp_us", [1147880044 * 1_000_000]).cast(input_dtype),
            pl.Series("timestamp_ns", [1147880044 * 1_000_000_000]).cast(input_dtype),
        ]
    ).lazy()

    exp_dt = datetime(2006, 5, 17, 15, 34, 4)
    expected = pl.DataFrame(
        [
            pl.Series("timestamp_d", [date(2006, 5, 17)]),
            pl.Series("timestamp_s", [exp_dt]),  # s is no Polars dtype, defaults to us
            pl.Series("timestamp_ms", [exp_dt]).cast(pl.Datetime("ms")),
            pl.Series("timestamp_us", [exp_dt]),  # us is Polars Datetime default
            pl.Series("timestamp_ns", [exp_dt]).cast(pl.Datetime("ns")),
        ]
    )

    ldf_result = ldf.select(
        [
            pl.from_epoch(pl.col("timestamp_d"), unit="d"),
            pl.from_epoch(pl.col("timestamp_s"), unit="s"),
            pl.from_epoch(pl.col("timestamp_ms"), unit="ms"),
            pl.from_epoch(pl.col("timestamp_us"), unit="us"),
            pl.from_epoch(pl.col("timestamp_ns"), unit="ns"),
        ]
    ).collect()

    assert_frame_equal(ldf_result, expected)

    ts_col = pl.col("timestamp_s")
    with pytest.raises(ValueError):
        _ = ldf.select(pl.from_epoch(ts_col, unit="s2"))  # type: ignore[call-overload]


def test_from_epoch_seq_input() -> None:
    seq_input = [1147880044]
    expected = pl.Series([datetime(2006, 5, 17, 15, 34, 4)])
    result = pl.from_epoch(seq_input)
    assert_series_equal(result, expected)


def test_cumagg_types() -> None:
    lf = pl.DataFrame({"a": [1, 2], "b": [True, False], "c": [1.3, 2.4]}).lazy()
    cumsum_lf = lf.select(
        [pl.col("a").cumsum(), pl.col("b").cumsum(), pl.col("c").cumsum()]
    )
    assert cumsum_lf.schema["a"] == pl.Int64
    assert cumsum_lf.schema["b"] == pl.UInt32
    assert cumsum_lf.schema["c"] == pl.Float64
    collected_cumsum_lf = cumsum_lf.collect()
    assert collected_cumsum_lf.schema == cumsum_lf.schema

    cumprod_lf = lf.select(
        [
            pl.col("a").cast(pl.UInt64).cumprod(),
            pl.col("b").cumprod(),
            pl.col("c").cumprod(),
        ]
    )
    assert cumprod_lf.schema["a"] == pl.UInt64
    assert cumprod_lf.schema["b"] == pl.Int64
    assert cumprod_lf.schema["c"] == pl.Float64
    collected_cumprod_lf = cumprod_lf.collect()
    assert collected_cumprod_lf.schema == cumprod_lf.schema
