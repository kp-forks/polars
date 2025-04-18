from __future__ import annotations

import io

import pytest

import polars as pl
from polars.testing import assert_frame_equal


def test_categorical_outer_join() -> None:
    with pl.StringCache():
        df1 = pl.DataFrame(
            [
                pl.Series("key1", [42]),
                pl.Series("key2", ["bar"], dtype=pl.Categorical),
                pl.Series("val1", [1]),
            ]
        ).lazy()

        df2 = pl.DataFrame(
            [
                pl.Series("key1", [42]),
                pl.Series("key2", ["bar"], dtype=pl.Categorical),
                pl.Series("val2", [2]),
            ]
        ).lazy()

        expected = pl.DataFrame(
            {"key1": [42], "key2": ["bar"], "val1": [1], "val2": [2]},
            schema_overrides={"key2": pl.Categorical},
        )

    out = df1.join(df2, on=["key1", "key2"], how="outer").collect()
    assert_frame_equal(out, expected)

    with pl.StringCache():
        dfa = pl.DataFrame(
            [
                pl.Series("key", ["foo", "bar"], dtype=pl.Categorical),
                pl.Series("val1", [3, 1]),
            ]
        )
        dfb = pl.DataFrame(
            [
                pl.Series("key", ["bar", "baz"], dtype=pl.Categorical),
                pl.Series("val2", [6, 8]),
            ]
        )

    df = dfa.join(dfb, on="key", how="outer")
    # the cast is important to test the rev map
    assert df["key"].cast(pl.Utf8).to_list() == ["bar", "baz", "foo"]


def test_read_csv_categorical() -> None:
    f = io.BytesIO()
    f.write(b"col1,col2,col3,col4,col5,col6\n'foo',2,3,4,5,6\n'bar',8,9,10,11,12")
    f.seek(0)
    df = pl.read_csv(f, has_header=True, dtypes={"col1": pl.Categorical})
    assert df["col1"].dtype == pl.Categorical


def test_cat_to_dummies() -> None:
    df = pl.DataFrame({"foo": [1, 2, 3, 4], "bar": ["a", "b", "a", "c"]})
    df = df.with_columns(pl.col("bar").cast(pl.Categorical))
    assert pl.get_dummies(df).to_dict(False) == {
        "foo_1": [1, 0, 0, 0],
        "foo_2": [0, 1, 0, 0],
        "foo_3": [0, 0, 1, 0],
        "foo_4": [0, 0, 0, 1],
        "bar_a": [1, 0, 1, 0],
        "bar_b": [0, 1, 0, 0],
        "bar_c": [0, 0, 0, 1],
    }


def test_comp_categorical_lit_dtype() -> None:
    df = pl.DataFrame(
        data={"column": ["a", "b", "e"], "values": [1, 5, 9]},
        schema=[("column", pl.Categorical), ("more", pl.Int32)],
    )

    assert df.with_columns(
        pl.when(pl.col("column") == "e")
        .then("d")
        .otherwise(pl.col("column"))
        .alias("column")
    ).dtypes == [pl.Categorical, pl.Int32]


def test_categorical_describe_3487() -> None:
    # test if we don't err
    df = pl.DataFrame({"cats": ["a", "b"]})
    df = df.with_columns(pl.col("cats").cast(pl.Categorical))
    df.describe()


def test_categorical_is_in_list() -> None:
    # this requires type coercion to cast.
    # we should not cast within the function as this would be expensive within a groupby
    # context that would be a cast per group
    with pl.StringCache():
        df = pl.DataFrame(
            {"a": [1, 2, 3, 1, 2], "b": ["a", "b", "c", "d", "e"]}
        ).with_columns(pl.col("b").cast(pl.Categorical))

        cat_list = ("a", "b", "c")
        assert df.filter(pl.col("b").is_in(cat_list)).to_dict(False) == {
            "a": [1, 2, 3],
            "b": ["a", "b", "c"],
        }


def test_unset_sorted_on_append() -> None:
    with pl.StringCache():
        df1 = pl.DataFrame(
            [
                pl.Series("key", ["a", "b", "a", "b"], dtype=pl.Categorical),
                pl.Series("val", [1, 2, 3, 4]),
            ]
        ).sort("key")
        df2 = pl.DataFrame(
            [
                pl.Series("key", ["a", "b", "a", "b"], dtype=pl.Categorical),
                pl.Series("val", [5, 6, 7, 8]),
            ]
        ).sort("key")
        df = pl.concat([df1, df2], rechunk=False)
        assert df.groupby("key").count()["count"].to_list() == [4, 4]


def test_categorical_error_on_local_cmp() -> None:
    df_cat = pl.DataFrame(
        [
            pl.Series("a_cat", ["c", "a", "b", "c", "b"], dtype=pl.Categorical),
            pl.Series("b_cat", ["F", "G", "E", "G", "G"], dtype=pl.Categorical),
        ]
    )
    with pytest.raises(
        pl.ComputeError,
        match=(
            "Cannot compare categoricals originating from different sources. Consider"
            " setting a global string cache."
        ),
    ):
        df_cat.filter(pl.col("a_cat") == pl.col("b_cat"))


def test_cast_null_to_categorical() -> None:
    assert pl.DataFrame().with_columns(
        [pl.lit(None).cast(pl.Categorical).alias("nullable_enum")]
    ).dtypes == [pl.Categorical]


def test_shift_and_fill() -> None:
    df = pl.DataFrame({"a": ["a", "b"]}).with_columns(
        [pl.col("a").cast(pl.Categorical)]
    )

    s = df.with_columns(pl.col("a").shift_and_fill(1, "c"))["a"]
    assert s.dtype == pl.Categorical
    assert s.to_list() == ["c", "a"]


def test_merge_lit_under_global_cache_4491() -> None:
    with pl.StringCache():
        df = pl.DataFrame(
            [
                pl.Series("label", ["foo", "bar"], dtype=pl.Categorical),
                pl.Series("value", [3, 9]),
            ]
        )
        assert df.with_columns(
            pl.when(pl.col("value") > 5)
            .then(pl.col("label"))
            .otherwise(pl.lit(None, pl.Categorical))
        ).to_dict(False) == {"label": [None, "bar"], "value": [3, 9]}


def test_nested_cache_composition() -> None:
    # very artificial example/test, but validates the behaviour
    # of  ested StringCache scopes, which we want to play well
    # with each other when composing more complex pipelines.

    assert pl.using_string_cache() is False

    # function representing a composable stage of a pipeline; it implements
    # an inner scope for the case where it is called by itself, but when
    # called as part of a larger series of ops it should not invalidate
    # the string cache (the outermost scope should be respected).
    def create_lazy(data: dict) -> pl.LazyFrame:  # type: ignore[type-arg]
        with pl.StringCache():
            df = pl.DataFrame({"a": ["foo", "bar", "ham"], "b": [1, 2, 3]})
            lf = df.with_columns(pl.col("a").cast(pl.Categorical)).lazy()

        # confirm that scope-exit does NOT invalidate the
        # cache yet, as an outer context is still active
        assert pl.using_string_cache() is True
        return lf

    # this outer scope should be respected
    with pl.StringCache():
        lf1 = create_lazy({"a": ["foo", "bar", "ham"], "b": [1, 2, 3]})
        lf2 = create_lazy({"a": ["spam", "foo", "eggs"], "c": [3, 2, 2]})

        res = lf1.join(lf2, on="a", how="inner").collect().rows()
        assert sorted(res) == [("bar", 2, 2), ("foo", 1, 1), ("ham", 3, 3)]

    # no other scope active; NOW we expect the cache to have been invalidated
    assert pl.using_string_cache() is False


def test_categorical_list_concat_4762() -> None:
    df = pl.DataFrame({"x": "a"})
    expected = {"x": [["a", "a"]]}

    q = df.lazy().select([pl.concat_list([pl.col("x").cast(pl.Categorical)] * 2)])
    with pl.StringCache():
        assert q.collect().to_dict(False) == expected


def test_categorical_max_null_5437() -> None:
    assert (
        pl.DataFrame({"strings": ["c", "b", "a", "c"], "values": [0, 1, 2, 3]})
        .with_columns(pl.col("strings").cast(pl.Categorical).alias("cats"))
        .select(pl.all().max())
    ).to_dict(False) == {"strings": ["c"], "values": [3], "cats": [None]}


def test_categorical_in_struct_nulls() -> None:
    s = pl.Series(
        "job", ["doctor", "waiter", None, None, None, "doctor"], pl.Categorical
    )
    df = pl.DataFrame([s])
    s = (df.select(pl.col("job").value_counts(sort=True)))["job"]

    assert s[0] == {"job": None, "counts": 3}
    assert s[1] == {"job": "doctor", "counts": 2}
    assert s[2] == {"job": "waiter", "counts": 1}


def test_cast_inner_categorical() -> None:
    dtype = pl.List(pl.Categorical)
    out = pl.Series("foo", [["a"], ["a", "b"]]).cast(dtype)
    assert out.dtype == dtype
    assert out.to_list() == [["a"], ["a", "b"]]

    with pytest.raises(
        pl.ComputeError, match=r"Casting to 'Categorical' not allowed in 'arr.eval'"
    ):
        pl.Series("foo", [["a", "b"], ["a", "b"]]).arr.eval(
            pl.element().cast(pl.Categorical)
        )


def test_stringcache() -> None:
    N = 1_500
    with pl.StringCache():
        # create a reasonable sized columns so the categorical map is reallocated
        df = pl.DataFrame({"cats": pl.arange(0, N, eager=True)}).select(
            [pl.col("cats").cast(pl.Utf8).cast(pl.Categorical)]
        )
        assert df.filter(pl.col("cats").is_in(["1", "2"])).to_dict(False) == {
            "cats": ["1", "2"]
        }
