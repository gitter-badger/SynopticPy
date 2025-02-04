## Brian Blaylock
## August 13, 2020    COVID-19 Era

"""
==================
👨🏻‍💻 Services
==================
Get mesonet data from the `Synoptic API services 
<https://developers.synopticdata.com/>`_ and return data as a
Pandas.DataFrame. Requires a `Synoptic API token 
<https://synopticlabs.org/api/guides/?getstarted>`_

.. tip::

    Before you get started, please become familiar with the
    `Synoptic API developers documentation 
    <https://developers.synopticdata.com/mesonet/v2/>`_.


Station Selector Parameters
---------------------------
The fundamental method for specifying the data you query is done with
**station selector arguments**. Below are some of the more common 
paramaters. Read `Station Selectors
<https://developers.synopticdata.com/mesonet/v2/station-selectors/>`__ 
in the API documents for all options and capabilities.

    stid : str or list
        Specify which stations you want to get data for by Station ID.
        May be a single ID or list of IDs.
        ``['KSLC', 'UKBKB', 'KMRY']`` *or* ``'KSLC'``
    state : str or list
        String or list of abbreviated state strings, 
        i.e. ``['UT','CA']``
    radius : str
        Only return stations within a great-circle distance from a 
        specified lat/lon point or station (by STID). May be in form
        ``"lat,lon,miles"`` *or* ``"stid,miles"``
    vars : str or list
        Filter stations by the variables they report.
        i.e., ``['air_temp', 'wind_speed', 'wind_direction', etc.]``
        Look at the `docs for more variables 
        <https://developers.synopticdata.com/about/station-variables/>`_.
    varsoperator : {'and', 'or'}
        Define how  ``vars`` is understood.
        Default ``'or'`` means any station with any variable is used.
        However, ``'and'`` means a station must report every variable 
        to be listed.
    network - int
        Network ID number. See `network API service 
        <https://developers.synopticdata.com/about/station-providers/>`_
        for more information.
    limit : int
        Specify how many of the closest stations you want to receive.
        ``limit=1`` will only return the nearest station.
    bbox : [lonmin, latmin, lonmax, lonmin]
        Get stations within a bounding box.
    
Other Common Parameters
-----------------------
    units : {'metric', 'english'}
        See `documentation 
        <https://developers.synopticdata.com/mesonet/v2/stations/latest/>`_
        for more details on custom units selection.
        An example of a custom unit is ``units='temp|F'`` to set just
        the temperature to degrees Fahrenheit.
        For ``units='temp|K,pres|mb'``,temperatures to Kelvin and
        pressures will be in hecto Pascals (mb, or hPa).
    obtimezone : {'UTC', 'local'}
        Specify the time to be UTC or the station's local time.
    status : {'active', 'inactive'}
        Specify if the statation is active or inactive.

.. note::
    These Datetimes have timezone information. When plotting,
    I haven't had issues with Pandas 1.1.0 and matplotlib 3.3.0,
    but for earlier version, matplotlib doesn't like the DatetimeIndex
    with timezone information. In that case, you can remove the datetime
    information with something like this:
    
    .. code-block:: python

        df.index.tz_localize(None)

"""
import sys
import warnings
from datetime import datetime, timedelta

import requests
import urllib
import numpy as np
import pandas as pd

from synoptic.get_token import config

# Available API Services
# https://developers.synopticdata.com/mesonet/v2/
_service = {"auth", "networks", "networktypes", "variables", "qctypes"}
_stations = {"metadata", "timeseries", "precipitation", "nearesttime", "latest"}
_service.update(_stations)

# Station Selector Parameters Set
_stn_selector = {
    "stid",
    "country",
    "state",
    "country",
    "status",
    "nwszone",
    "nwsfirezone",
    "cwa",
    "gacc",
    "subgacc",
    "vars",
    "varsoperator",
    "network",
    "radius",
    "limit",
    "bbox",
    "fields",
}


def spddir_to_uv(wspd, wdir):
    """
    Calculate the u and v wind components from wind speed and direction.

    Parameters
    ----------
    wspd, wdir : array_like
        Arrays of wind speed and wind direction (in degrees)

    Returns
    -------
    u and v wind components

    """
    if isinstance(wspd, list) or isinstance(wdir, list):
        wspd = np.array(wspd, dtype=float)
        wdir = np.array(wdir, dtype=float)

    rad = 4.0 * np.arctan(1) / 180.0
    u = -wspd * np.sin(rad * wdir)
    v = -wspd * np.cos(rad * wdir)

    # If the speed is zero, then u and v should be set to zero (not NaN)
    if hasattr(u, "__len__"):
        u[np.where(wspd == 0)] = 0
        v[np.where(wspd == 0)] = 0
    elif wspd == 0:
        u = float(0)
        v = float(0)

    return np.round(u, 3), np.round(v, 3)


# Rename "set_1" and "value_1" names is a convience I prefer.
## You can turn these off in your requests by setting `rename_set_1`
## and `rename_value_1` to False in your function call where applicable.
def _rename_set_1(df):
    """
    Rename Variable Columns Names

    Remove the 'set_1' and 'set_1d' from column names
    Sets 2+ will retain their full names.
    The user should refer to SENSOR_VARIABLES to see which
    variables are derived

    """

    ## Get list of current column names
    dummy_columns = list(df.columns)

    # Remove '_set_1' and '_set_1d' from column name
    var_names = [
        "_".join(v.split("_")[:-2]) if "_set_1" in v else v for v in dummy_columns
    ]

    # Number of observations in each column
    obs_count = list(df.count())

    # Sometimes, set_1 and set_1d are both returned. In that
    # case, we need to determin which column has the most
    # observations and use that as the main variable. The set
    # with fewer data will retain the 'set_1' or 'set_1d' label.
    renames = {}
    for i, name in enumerate(var_names):
        # Determine all indices this variable type is located
        var_bool = [v.startswith(name + "_set_1") for v in dummy_columns]
        var_idx = np.where(var_bool)[0]

        if len(var_idx) == 1:
            # This variable is only listed once. Rename with var_name
            renames[dummy_columns[i]] = var_names[var_idx[0]]
        elif len(var_idx) > 1:
            # This variable is listed more than once.
            # Determine which set has the most non-NaN data and
            # rename that column as var_name.
            max_idx = var_idx[np.argmax([obs_count[i] for i in var_idx])]
            if max_idx == i:
                # If the current iteration matches the var_idx with
                # the most data, rename the column without set number.
                renames[dummy_columns[i]] = var_names[max_idx]
            else:
                # If the current iteration does not match the var_idx
                # with the most data, then retain the original column
                # name with the set number.
                renames[dummy_columns[i]] = dummy_columns[i]
        else:
            # This case should only occur during my testing.
            renames[dummy_columns[i]] = dummy_columns[i]
    df.rename(columns=renames, inplace=True)
    df.attrs["RENAMED"] = renames
    return df


def _rename_value_1(df):
    """
    Rename Variable Row (index) Names

    Remove the ``value_1`` and ``value_1d`` from column names.
    Values 2+ will retain their full names. If both
    ``value_1`` and ``value_1d`` are returned, the newest observation
    will be used as the main index while the older will preserve the
    ``_value`` label.

    The user should refer to SENSOR_VARIABLES to see which
    variables are derived.

    """

    ## Get list of current column names
    dummy_rows = list(df.index)

    # Remove '_set_1' and '_set_1d' from column name
    var_names = [
        "_".join(v.split("_")[:-2]) if "_value_1" in v else v for v in dummy_rows
    ]

    # Sometimes, value_1 and value_1d are both returned. In that
    # case, we need to determine which is the *newest*
    # observations and use that as the main variable. The older value
    # will retain the 'value_1' or 'value_1d' label.

    renames = {}
    for i, name in enumerate(var_names):
        # Determine all indices this variable type is located
        var_bool = [v.startswith(name + "_value_1") for v in dummy_rows]
        var_idx = np.where(var_bool)[0]

        if len(var_idx) == 1:
            # This variable is only listed once. Rename with var_name
            renames[dummy_rows[i]] = var_names[var_idx[0]]
        elif len(var_idx) > 1:
            # This variable is listed more than once.
            # Determine which observation is older and
            # rename that row as var_name.
            max_idx = var_idx[np.argmax([df.date_time.iloc[i] for i in var_idx])]
            if max_idx == i:
                # If the current iteration matches the var_idx with
                # the most data, rename the column without set number.
                renames[dummy_rows[i]] = var_names[max_idx]
            else:
                # If the current iteration does not match the var_idx
                # with the most data, then retain the original column
                # name with the set number.
                renames[dummy_rows[i]] = dummy_rows[i]
        else:
            # This case for row names that don't have _value in them (i.e., ELEVATION).
            renames[dummy_rows[i]] = dummy_rows[i]
    df.rename(index=renames, inplace=True)
    df.attrs["RENAMED"] = renames
    return df


def _parse_latest_nearesttime(data, rename_value_1):
    """
    Parsing JSON for ``latest`` and ``nearesttime`` is the same.

    """
    # Here's a dictionary to store all SENSOR_VARIABLES and RENAMED
    # WARNING: Some of these could be overwritten
    senvars = {}
    renamed = {}

    dfs = []
    for i in data["STATION"]:
        obs = i.pop("OBSERVATIONS")
        df = pd.DataFrame(obs)

        # Add other station info to the DataFrame (i.e., ELEVATION, latitude, etc.)
        for k, v in i.items():
            # Attempt to convert values to a float, if possible.
            # (i.e, latitude, longitude, elevation, MNET_ID, etc.)
            try:
                v = float(v)
            except:
                pass
            if k in ["LATITUDE", "LONGITUDE"]:
                # lat/lon is lowercase for CF compliant variable name
                df[k.lower()] = [None, v]
            else:
                df[k] = [None, v]

        # Break wind into U and V components, if speed and direction are available
        senvars = i["SENSOR_VARIABLES"]
        if all([i in senvars for i in ["wind_speed", "wind_direction"]]):
            for i_spd, i_dir in zip(
                senvars["wind_speed"].keys(), senvars["wind_direction"].keys()
            ):
                if obs[i_spd]["date_time"] == obs[i_spd]["date_time"]:
                    wspd = obs[i_spd]["value"]
                    wdir = obs[i_dir]["value"]
                    u, v = spddir_to_uv(wspd, wdir)
                    this_set = "_".join(i_spd.split("_")[-2:])
                    df[f"wind_u_{this_set}"] = [obs[i_spd]["date_time"], u]
                    df[f"wind_v_{this_set}"] = [obs[i_spd]["date_time"], v]

        # Convert date_time to datetime object
        df.loc["date_time"] = pd.to_datetime(df.loc["date_time"])

        df = df.transpose().sort_index()

        if rename_value_1:
            # Rename Index Rows (remove _value_1 label)
            df = _rename_value_1(df)
            renamed = {**renamed, **df.attrs["RENAMED"]}

        rename = dict(date_time=f"{i['STID']}_date_time", value=i["STID"])
        df.rename(columns=rename, inplace=True)
        senvars = {**senvars, **i["SENSOR_VARIABLES"]}
        dfs.append(df)

    df = pd.concat(dfs, axis=1)

    df.attrs["STATIONS"] = [i for i in df.columns if "date_time" not in i]
    df.attrs["DATETIMES"] = [i for i in df.columns if "date_time" in i]
    df.attrs["UNITS"] = data["UNITS"]
    df.attrs["SUMMARY"] = data["SUMMARY"]
    df.attrs["QC_SUMMARY"] = data["QC_SUMMARY"]
    df.attrs["RENAMED"] = renamed
    df.attrs["SENSOR_VARIABLES"] = senvars

    return df


# =======================================================================
# =======================================================================


def synoptic_api(
    service,
    verbose=config["default"].get("hide_token", True),
    hide_token=config["default"].get("hide_token", False),
    **params,
):
    """
    Request data from the Synoptic API. Returns a **requests** object.

    References
    ----------
    - https://developers.synopticdata.com/mesonet/v2/
    - https://developers.synopticdata.com/mesonet/explorer/

    Parameters
    ----------
    service : str
        API service to use, including {'auth', 'latest', 'metadata',
        'nearesttime', 'networks', 'networktypes', 'precipitation',
        'qctypes', 'timeseries', 'variables'}
    verbose : {True, False}
        Print extra details to the screen.
    hide_token : bool
        If True, the token will be hidden when API URL is printed.
    \*\*params : keyword arguments
        API request parameters (arguments).
        Lists will be converted to a comma-separated string.
        Datetimes (datetime or pandas) will be parsed by f-string to
        YYYYmmddHHMM.

    Returns
    -------
    A ``requests.models.Response`` object from ``requests.get(URL, params)``

    Examples
    --------
    To read the json data for metadata for a station

    .. code:: python

        synoptic_api('metadata', stid='WBB').json()

    .. code:: python

        synoptic_api('metadata', stid=['WBB', 'KSLC']).json()

    """
    help_url = "https://developers.synopticdata.com/mesonet/v2/"
    assert (
        service in _service
    ), f"`service` must be one of {_service}. See API documentation {help_url}"

    ## Service URL
    ##------------
    root = "https://api.synopticdata.com/v2"

    if service in _stations:
        URL = f"{root}/stations/{service}"
    else:
        URL = f"{root}/{service}"

    ## Set API token
    ##--------------
    ## Default token is set in the ~/.config/SyonpticPy/config.toml file,
    ## But you may overwrite it by passing the keyward argument `token=`
    params.setdefault("token", config["default"].get("token"))

    ## Parse parameters
    ##-----------------
    # Change some keyword parameters to the appropriate request format

    ## 1) Force all param keys to be lower case
    params = {k.lower(): v for k, v in params.items()}

    ## 2) Join lists as comma separated strings.
    ##    For example, stid=['KSLC', 'KMRY'] --> stid='KSLC,KRMY'.
    ##                 radius=[40, -100, 10] --> radius='40,-100,10'
    for key, value in params.items():
        if isinstance(value, list) and key not in ["obrange"]:
            params[key] = ",".join([str(i) for i in value])

    ## 3) Datetimes should be converted to string: 'YYYYmmddHHMM' (obrange is 'YYYYmmdd')
    for i in ["start", "end", "expire", "attime"]:
        if i in params:
            date = params[i]
            if isinstance(date, str) and len(date) >= 8 and date.isnumeric():
                # Put into a string that is recognized by Pandas
                date = f"{date[:8]} {date[8:]}"  # formatted as "YYYYmmdd HH"
            try:
                # Try to convert input to a Pandas Datetime
                params[i] = pd.to_datetime(date)
            except:
                warnings.warn(f"🐼 Pandas could not parse [{i}={date}] as a datetime.")
            # Format the datetime as a Synoptic-recognized string.
            params[i] = f"{params[i]:%Y%m%d%H%M}"
    ## 4) Special case for 'obrange' parameter dates...
    if "obrange" in params and not isinstance(params["obrange"], str):
        # obrange could be one date or a list of two dates.
        if not hasattr(params["obrange"], "__len__"):
            params["obrange"] = [params["obrange"]]
        params["obrange"] = ",".join([f"{i:%Y%m%d}" for i in params["obrange"]])

    ## 5) Timedeltas should be converted to int in minutes...
    for i in ["recent", "within"]:
        if i in params:
            dt = params[i]
            if isinstance(dt, (int, float)):
                dt = timedelta(minutes=dt)
            try:
                # Try to convert input to Pandas timedelta
                params[i] = pd.to_timedelta(dt)
            except:
                warnings.warn(f"🐼 Pandas could not parse [{i}={dt}] as a timedelta.")
            # Format the datetime as a Synoptic-recognized int of minutes
            to_minutes = np.ceil(params[i] / timedelta(minutes=1)).astype(int)
            params[i] = to_minutes
            if verbose:
                print(f"Checking for data {i}={params[i]} minutes.")

    ########################
    # Make the API request #
    ########################
    f = requests.get(URL, params)

    if service == "auth":
        return f

    # Check Returned Data
    code = f.json()["SUMMARY"]["RESPONSE_CODE"]
    msg = f.json()["SUMMARY"]["RESPONSE_MESSAGE"]
    decode_url = urllib.parse.unquote(f.url)

    assert code == 1, f"🛑 There are errors in the API request {decode_url}. {msg}"

    if verbose:
        if hide_token:
            token_idx = decode_url.find("token=")
            decode_url = decode_url.replace(
                decode_url[token_idx : token_idx + 6 + 32], "token=🙈HIDDEN"
            )
        print(f"\n 🚚💨 Speedy Delivery from Synoptic API [{service}]: {decode_url}\n")

    return f


def stations_metadata(verbose=config["default"].get("verbose", True), **params):
    """
    Get station metadata for stations as a Pandas DataFrame.

    https://developers.synopticdata.com/mesonet/v2/stations/metadata/

    Parameters
    ----------
    \*\*params : keyword arguments
        Synoptic API arguments used to specify the data request.
        e.g., sensorvars, obrange, obtimezone, etc.

    Others: STATION SELECTION PARAMETERS
    https://developers.synopticdata.com/mesonet/v2/station-selectors/

    """
    assert any(
        [i in _stn_selector for i in params]
    ), f"🤔 Please assign a station selector (i.e., {_stn_selector})"

    # Get the data
    web = synoptic_api("metadata", verbose=verbose, **params)
    data = web.json()

    # Initialize a DataFrame
    df = pd.DataFrame(data["STATION"], index=[i["STID"] for i in data["STATION"]])

    # Convert data to numeric values (if possible)
    df = df.apply(pd.to_numeric, errors="ignore")

    # Deal with "Period Of Record" dictionary
    df = pd.concat([df, df.PERIOD_OF_RECORD.apply(pd.Series)], axis=1)
    df[["start", "end"]] = df[["start", "end"]].apply(pd.to_datetime)

    # Rename some fields.
    # latitude and longitude are made lowercase to conform to CF standard
    df.drop(columns=["PERIOD_OF_RECORD"], inplace=True)
    df.rename(
        columns=dict(
            LATITUDE="latitude",
            LONGITUDE="longitude",
            start="RECORD_START",
            end="RECORD_END",
        ),
        inplace=True,
    )

    df.attrs["URL"] = urllib.parse.unquote(web.url)
    df.attrs["UNITS"] = {"ELEVATION": "ft"}
    df.attrs["SUMMARY"] = data["SUMMARY"]
    df.attrs["params"] = params
    df.attrs["service"] = "stations_metadata"
    return df.transpose().sort_index()


def stations_timeseries(
    verbose=config["default"].get("verbose", True),
    rename_set_1=config["default"].get("rename_set_1", True),
    **params,
):
    """
    Get station data for time series.

    https://developers.synopticdata.com/mesonet/v2/stations/timeseries/

    Parameters
    ----------
    rename_set_1 : bool

        - True: Rename the DataFrame columns to not include the set_1
          or set_1d in the name. I prefer these names to more easily
          key in on the variables I want.
          Where there are both set_1 and set_1d for a variable, only the
          column with the most non-NaN values will be renamed.
        - False: Perserve the original column names.

        .. note::
            Observations returned from the Synoptic API are returned
            with set numbers ("air_temp_set_1", "air_temp_set_2",
            "dew_point_temperature_set_1d", etc.). The set number refers
            to a different observing method (maybe a station has two
            temperature sensors or two heights). The 'd' means the
            variable was derived from other data.

            In my general use, I don't usually care which variables
            are derived, and just want the variable that provides the
            most data. Almost always, I want to use set_1.

        .. note::
            The DataFrame attribute 'RENAMED' is provided to
            show how the columns were renamed.
            You may also look at the 'SENSOR_VARIABLES' attribute for
            more specific information, like how each set is derived.

    \*\*params : keyword arguments
        Synoptic API arguments used to specify the data request.
        **Must include** ``start`` and ``end`` argument *or* ``recent``.
    start, end : datetime, pandas Timestamp, or pandas.to_datetime-parsable str
        Start and end of time series
    recent : int, timedelta, or Pandas Timedelta
        If int, given as minutes for recent observations.
        Or, give a timedelta or pandas timedelta ``recent=timedelta(day=2)`
        and ``recent=pd.to_timedelta('1D')``.
        Or, give a pandas-recognized timedelta-string, like '1W' for one
        week, '1h' for one hour, etc. ``recent='1D'`` for one day.
        See https://pandas.pydata.org/pandas-docs/stable/reference/api/pandas.to_timedelta.html
        for additional units.

    Other params include ``obtimezone``, ``units``, and any Station
    Selector parameter.

    Examples
    --------

    .. code:: python

        stations_timeseries(stid='WBB', recent=100)
        stations_timeseries(radius='UKBKB,10', vars='air_temp', recent=100)
        stations_timeseries(stid='KMRY', recent=60, vars='air_temp', obtimezone='Local', units='temp|F')

    .. code:: python

        import matplotlib.pyplot as plt
        from matplotlib.dates import DateFormatter
        df = stations_timeseries(stid='WBB', recent=300)
        plt.plot(df['air_temp'])
        plt.plot(df['dew_point_temperature'])
        plt.gca().xaxis.set_major_formatter(DateFormatter('%b %d %H:%M'))
        plt.legend()

    """
    check1 = "start" in params and "end" in params
    check2 = "recent" in params
    assert check1 or check2, "🤔 `start` and `end` *or* `recent` is required"
    assert any(
        [i in _stn_selector for i in params]
    ), f"🤔 Please assign a station selector (i.e., {_stn_selector})"

    # Get the data
    web = synoptic_api("timeseries", verbose=verbose, **params)
    data = web.json()

    # Build a separate pandas.DataFrame for each station.
    Z = []
    for stn in data["STATION"]:
        obs = stn.pop("OBSERVATIONS")
        senvars = stn.pop("SENSOR_VARIABLES")

        # Turn Data into a DataFrame
        df = pd.DataFrame(obs).set_index("date_time")

        # Remaining data in dict will be returned as attribute
        df.attrs = stn

        # Convert datetime index string to datetime
        df.index = pd.to_datetime(df.index)

        # Sort Column order alphabetically
        df = df.reindex(columns=df.columns.sort_values())

        # Break wind into U and V components, if speed and direction are available
        if all(["wind_speed" in senvars, "wind_direction" in senvars]):
            for i_spd, i_dir in zip(
                senvars["wind_speed"].keys(), senvars["wind_direction"].keys()
            ):
                u, v = spddir_to_uv(obs[i_spd], obs[i_dir])
                this_set = "_".join(i_spd.split("_")[-2:])
                df[f"wind_u_{this_set}"] = u
                df[f"wind_v_{this_set}"] = v
                data["UNITS"]["wind_u"] = data["UNITS"]["wind_speed"]
                data["UNITS"]["wind_v"] = data["UNITS"]["wind_speed"]

        if rename_set_1:
            df = _rename_set_1(df)

        # Drop Row if all data is NaN/None
        df.dropna(how="all", inplace=True)

        # In the DataFrame attributes, Convert some strings to float/int
        # (i.e., ELEVATION, latitude, longitude) BUT NOT STID!
        for k, v in df.attrs.items():
            if isinstance(v, str) and k not in ["STID"]:
                try:
                    n = float(v)
                    if n.is_integer():
                        df.attrs[k] = int(n)
                    else:
                        df.attrs[k] = n
                except:
                    pass

        if len(df.columns) != len(set(df.columns)):
            warnings.warn("🤹🏼‍♂️ DataFrame contains duplicate column names.")

        # Rename lat/lon to lowercase to match CF convenctions
        df.attrs["latitude"] = df.attrs.pop("LATITUDE")
        df.attrs["longitude"] = df.attrs.pop("LONGITUDE")

        # Include other info
        for i in data.keys():
            if i != "STATION":
                df.attrs[i] = data[i]
        df.attrs["SENSOR_VARIABLES"] = senvars
        df.attrs["params"] = params
        df.attrs["service"] = "stations_timeseries"

        Z.append(df)

    if len(Z) == 1:
        return Z[0]
    else:
        if verbose:
            print(f'Returned [{len(Z)}] stations. {[i.attrs["STID"] for i in Z]}')
        return Z


def stations_nearesttime(
    verbose=config["default"].get("verbose", True),
    rename_value_1=config["default"].get("rename_value_1", True),
    **params,
):
    """
    Get station data nearest a datetime. (Very similar to the latest service.)

    https://developers.synopticdata.com/mesonet/v2/stations/nearesttime/

    Parameters
    ----------
    rename_value_1 : bool

        - True: Rename the DataFrame index to not include the value_1
          or value_1d in the name. I prefer these names to more easily
          key in on the variables I want.
          Where there are both value_1 and value_1d for a variable, only
          the most recent value will be renamed.
        - False: Perserve the original index names.

    \*\*params : keyword arguments
        Synoptic API arguments used to specify the data request.
        **Must include** ``attime`` and ``within``
    attime : datetime, pandas Timestamp, or pandas.to_datetime-parsable str
        Datetime you want to the the nearest observations for.
    within : int, timedelta, or Pandas Timedelta
        How long ago is the oldest observation you want to receive?
        If int, given as minutes for recent observations.
        Or, give a timedelta or pandas timedelta ``recent=timedelta(day=2)`
        and ``recent=pd.to_timedelta('1D')``.
        Or, give a pandas-recognized timedelta-string, like '1W' for one
        week, '1h' for one hour, etc. ``recent='1D'`` for one day.
        See https://pandas.pydata.org/pandas-docs/stable/reference/api/pandas.to_timedelta.html
        for additional units.

    Other params include ``obtimezone``, ``units``, and any Station
    Selector parameter.

    Examples
    --------
    .. code:: python

        stations_nearesttime(attime=datetime(2020,1,1), within=60, stid='WBB')

    """
    assert "attime" in params, "🤔 `attime` is a required parameter (datetime)."
    assert any(
        [i in _stn_selector for i in params]
    ), f"🤔 Please assign a station selector (i.e., {_stn_selector})"

    params.setdefault("within", 60)

    # Get the data
    web = synoptic_api("nearesttime", verbose=verbose, **params)
    data = web.json()

    df = _parse_latest_nearesttime(data, rename_value_1)
    df.attrs["params"] = params
    df.attrs["service"] = "stations_nearesttime"
    return df


def stations_latest(
    verbose=config["default"].get("verbose", True),
    rename_value_1=config["default"].get("rename_value_1", True),
    **params,
):
    """
    Get the latest station data. (Very similar to the nearesttime service.)

    https://developers.synopticdata.com/mesonet/v2/stations/latest/

    Parameters
    ----------
    rename_value_1 : bool
        Option to rename the DataFrame index to not include the
        ``value_1`` or ``value_1d`` in the name. I prefer that the
        column names strips this part of the string to more easily key
        in on the variables I want. For situations where there are both
        ``value_1`` and ``value_1d`` for a variable, only the most
        recent value will be renamed.

        - True: Strip ``value_1`` from the column variable names.
        - False: Perserve the original index names.

    \*\*params : keyword arguments
        Synoptic API arguments used to specify the data request.
        **Must include** ``within``.
    within : int, timedelta, or Pandas Timedelta
        If int, given as minutes for recent observations.
        Or, give a timedelta or pandas timedelta ``recent=timedelta(day=2)`
        and ``recent=pd.to_timedelta('1D')``.
        Or, give a pandas-recognized timedelta-string, like '1W' for one
        week, '1h' for one hour, etc. ``recent='1D'`` for one day.
        See https://pandas.pydata.org/pandas-docs/stable/reference/api/pandas.to_timedelta.html
        for additional units.

    Other params include ``obtimezone``, ``units``, and any Station
    Selector parameter.

    Examples
    --------

    .. code:: python

        stations_nearesttime(attime=datetime(2020,1,1), within=60, stid='WBB')

    """
    assert any(
        [i in _stn_selector for i in params]
    ), f"🤔 Please assign a station selector (i.e., {_stn_selector})"

    params.setdefault("within", 60)

    # Get the data
    web = synoptic_api("latest", verbose=verbose, **params)
    data = web.json()

    df = _parse_latest_nearesttime(data, rename_value_1)
    df.attrs["params"] = params
    df.attrs["service"] = "stations_latest"
    return df


def stations_precipitation(verbose=config["default"].get("verbose", True), **params):
    """
    Get the precipitation data.

    https://developers.synopticdata.com/mesonet/v2/stations/precipitation/

    Parameters
    ----------
    \*\*params : keyword arguments
        Synoptic API arguments used to specify the data request.
        Requires `start` and `end` *or* `recent`.

    Other params include ``obtimezone``, ``units``, and any Station
    Selector parameter.

    """
    print("🙋🏼‍♂️ HI! THIS FUNCTION IS NOT COMPLETED YET. WILL JUST RETURN JSON.")

    check1 = "start" in params and "end" in params
    check2 = "recent" in params
    assert check1 or check2, "🤔 `start` and `end` *or* `recent` is required"
    assert any(
        [i in _stn_selector for i in params]
    ), f"🤔 Please assign a station selector (i.e., {_stn_selector})"

    # Get the data
    web = synoptic_api("precipitation", verbose=verbose, **params)
    data = web.json()

    return data


def networks(verbose=config["default"].get("verbose", True), **params):
    """
    Return a DataFrame of available Networks and their metadata

    https://developers.synopticdata.com/mesonet/v2/networks/
    https://developers.synopticdata.com/about/station-network-type/

    Parameters
    ----------
    **param : keyword arguments
    id : int or list of int
        Filter by network number.
    shortname : str or list of str
        Network shortname, i.e. 'NWS/FAA', 'RAWS', 'UTAH DOT',

    """
    # Get the data
    web = synoptic_api("networks", verbose=verbose, **params)
    data = web.json()

    df = pd.DataFrame(data["MNET"])
    df["ID"] = df["ID"].astype(int)
    df["CATEGORY"] = df["CATEGORY"].astype(int)
    df["REPORTING_STATIONS"] = df["REPORTING_STATIONS"].astype(int)
    df.set_index("ID", inplace=True)
    df["LAST_OBSERVATION"] = pd.to_datetime(df.LAST_OBSERVATION)
    df.attrs["SUMMARY"] = data["SUMMARY"]
    df.attrs["params"] = params
    df.attrs["service"] = "networks"
    return df


def networktypes(verbose=config["default"].get("verbose", True), **params):
    """
    Get a DataFrame of network types

    https://developers.synopticdata.com/mesonet/v2/networktypes/
    https://developers.synopticdata.com/about/station-network-type/

    Parameters
    ----------
    \*\*params : keyword arguments
    id : int
        Select just the network type you want

    """

    # Get the data
    web = synoptic_api("networktypes", verbose=verbose, **params)
    data = web.json()

    df = pd.DataFrame(data["MNETCAT"])
    df.set_index("ID", inplace=True)
    df.attrs["SUMMARY"] = data["SUMMARY"]
    df.attrs["params"] = params
    df.attrs["service"] = "networktypes"
    return df


def variables(verbose=config["default"].get("verbose", True), **params):
    """
    Return a DataFrame of available station variables

    https://developers.synopticdata.com/mesonet/v2/variables/
    https://developers.synopticdata.com/mesonet/v2/api-variables/

    Parameters
    ----------
    **param : keyword arguments
        There are none for the 'variables' service.

    """
    # Get the data
    web = synoptic_api("variables", verbose=verbose, **params)
    data = web.json()

    df = pd.concat([pd.DataFrame(i) for i in data["VARIABLES"]], axis=1).transpose()
    # df.set_index('vid', inplace=True)
    df.attrs["SUMMARY"] = data["SUMMARY"]
    df.attrs["params"] = params
    df.attrs["service"] = "variables"
    return df


def qctypes(verbose=config["default"].get("verbose", True), **params):
    """
    Return a DataFrame of available quality control (QC) types

    https://developers.synopticdata.com/mesonet/v2/qctypes/
    https://developers.synopticdata.com/about/qc/

    Parameters
    ----------
    **param : keyword arguments
        Available parameters include ``id`` and ``shortname``

    """
    # Get the data
    web = synoptic_api("qctypes", verbose=verbose, **params)
    data = web.json()

    df = pd.DataFrame(data["QCTYPES"])
    df = df.apply(pd.to_numeric, errors="ignore")
    df.set_index("ID", inplace=True)
    df.sort_index(inplace=True)
    df.attrs["SUMMARY"] = data["SUMMARY"]
    df.attrs["params"] = params
    df.attrs["service"] = "qctypes"
    return df


def auth(helpme=True, verbose=config["default"].get("verbose", True), **params):
    """
    Return a DataFrame of authentication controls.

    https://developers.synopticdata.com/mesonet/v2/auth/
    https://developers.synopticdata.com/settings/

    Parameters
    ----------
    helpme : bool
        True - It might be easier to deal with generating new tokens
        and listing tokens on the web settings, so just return the
        URL to help you make these changes via web.
        False - Access the ``auth`` API service.
    **param : keyword arguments

    Some include the following

    disableToken : str
    list : {1, 0}
    expire : datetime

    Examples
    --------
    List all tokens

    .. code:: python

        auth(helpme=False, apikey='YOUR_API_KEY', list=1)

    Create new token (tokens are disabled after 10 years)

    .. code:: python

        auth(helpme=False, apikey='YOUR_API_KEY')

    Create new token with expiration date

    .. code:: python

        auth(helpme=False, apikey='YOUR_API_KEY', expire=datetime(2021,1,1))

    Disable a token (not sure why this doesn't do anything)

    .. code:: python

        auth(helpme=False, apikey='YOUR_API_KEY', disable='TOKEN')

    """
    if helpme:
        web = "https://developers.synopticdata.com/settings/"
        print(f"It's easier to manage these via the web settings: {web}")
    else:
        assert "apikey" in params, f"🛑 `apikey` is a required argument. {web}"
        web = synoptic_api("auth", verbose=verbose, **params)
        data = web.json()
        return data


# Other Services
# ---------------
# stations_precipitation : *NOT FINISHED
# stations_latency : *NOT CURRENTLY AVAILABLE
# stations_qcsegments : ???
