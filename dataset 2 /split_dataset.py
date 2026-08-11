"""
Splits trip_energy_dataset.csv into train/test sets, stratified across
vehicle_profile x road_type x load_state so both splits stay representative
of all 18 strata (see dataset_schema.md).
"""

import pandas as pd
from sklearn.model_selection import train_test_split

SOURCE_PATH = "trip_energy_dataset.csv"
TRAIN_PATH = "trip_energy_train.csv"
TEST_PATH = "trip_energy_test.csv"
TEST_SIZE = 0.20
RANDOM_STATE = 42


def split_dataset(
    source_path: str = SOURCE_PATH,
    train_path: str = TRAIN_PATH,
    test_path: str = TEST_PATH,
    test_size: float = TEST_SIZE,
    random_state: int = RANDOM_STATE,
):
    df = pd.read_csv(source_path)

    strata = df["vehicle_profile"] + "_" + df["road_type"] + "_" + df["load_state"]

    train_df, test_df = train_test_split(
        df, test_size=test_size, random_state=random_state, stratify=strata
    )

    train_df = train_df.sort_values("trip_id").reset_index(drop=True)
    test_df = test_df.sort_values("trip_id").reset_index(drop=True)

    train_df.to_csv(train_path, index=False)
    test_df.to_csv(test_path, index=False)

    return train_df, test_df


if __name__ == "__main__":
    train_df, test_df = split_dataset()
    print(f"train: {train_df.shape} -> {TRAIN_PATH}")
    print(f"test:  {test_df.shape} -> {TEST_PATH}")
