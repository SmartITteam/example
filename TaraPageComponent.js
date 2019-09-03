import React from "react";
import {useDispatch} from "redux-react-hook";
import {makeStyles} from "@material-ui/styles";
import Table from "../Components/Tara/Table";
import Paper from "@material-ui/core/Paper/Paper";
import Drawer from "@material-ui/core/Drawer/Drawer";
import Filters from "../Components/Tara/Filters";
import classNames from "classnames";
import TextField from "@material-ui/core/TextField/TextField";
import Icon from "@material-ui/core/Icon/Icon";
import useDataTable from "../hooks/useDataTable";
import AppliedFilters from "../Components/Transactions/AppliedFilters";
import {boxIcon} from "../svg";

const useStyles = makeStyles(theme => ({
  paper: {
    padding: '0px 20px',
    paddingTop: '20px'
  },
  filters:{
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    position: 'relative'
  },
  filterButton: {
    width: 50,
    textAlign: 'right'
  },
  input:{
    margin: 0,
    "& input, & label": {
      paddingLeft: 60
    }
  },
  list: {
    width: 550,
    padding: '20px'
  },
  basis: {
    position: 'absolute',
    zIndex: '2',
    backgroundColor: '#f1f1f1',
    lineHeight: '57px',
    width: 50,
    boxSizing: 'border-box',
    borderRadius: '3px 0 0 3px',
    marginRight: -8,
    fontSize: 14,
    fontWeight: 500
  },
  notFound: {
    height: 500,
    display: 'flex',
    flexDirection: 'column',
    justifyContent: 'center',
    alignItems: 'center',
    color: '#797979',
    "& h4": {
      color: '#626262',
      margin: 0,
      fontSize: 25,
      fontWeight: 400
    },
    "& span": {
      fontSize: 50
    },
    "& p": {
      marginTop: 7,
      fontSize: 14
    }
  }
}))

const Tara = (props) => {
  const {history, location} = props;
  const classes = useStyles();
  const dispatch = useDispatch();
  const mapState = React.useCallback((state) => {
    return state.tara
  }, []);
  const {
    sort,
    filters,
    data,
    total,
    handleChangePage,
    handleChangeRowsPerPage,
    onIdChange,
    basis,
    filtersOpen,
    openFilters,
    deleteFilter
  } = useDataTable('SET_TARA_FILTERS', mapState);

  React.useEffect(() => {
    dispatch({
      type: 'GET_TARA_ITEMS',
      payload: {
        limit: filters.rowsPerPage,
        offset: filters.rowsPerPage * (filters.page - 1),
        sort: {
            value: 'last',
            method: filters.sort
        },
        id: filters.id || '',
        status: filters.type || '',
        inv_id: filters.name ? filters.name.value : '',
        part: filters.part || '',
        provider: filters.provider || ''
      }
    })
  }, [filters, dispatch]);

  React.useEffect(() => {
    return () => {dispatch({
      type: 'CLEAR_TARA_FILTERS'
    })}
  }, [dispatch])
  return(
      <div>
        <Drawer anchor="right" open={filtersOpen} onClose={() => {openFilters(false)}}>
          <Filters className={classes.list} filters={filters} closeFilters={() => {openFilters(false)}}/>
        </Drawer>
        <h1 className="page-title">Таблица Тары</h1>
        <Paper className={classes.paper}>
          <div className={classes.filters}>
            <div className={classNames(classes.basis, 'z-depth-2')}>{basis}</div>
            <TextField
                className={classes.input}
                label="Поиск по id тары"
                fullWidth
                value={filters.id}
                margin="normal"
                variant="outlined"
                onChange={onIdChange}
            />
            <Icon className={classes.filterButton} onClick={() => {openFilters(true)}}>filter_list</Icon>
          </div>
          <AppliedFilters filters={filters} deleteFilter={deleteFilter}/>
          { data && data.length > 0 ?
              <Table
              data={data}
              history={history}
              sort={sort}
              location={location}
              filters={filters}
              total={total}
              handleChangePage={handleChangePage}
              handleChangeRowsPerPage={handleChangeRowsPerPage}/>
              : (
                  <div className={classes.notFound}>
                    <Icon>{boxIcon}</Icon>
                    <h4>Не найдено</h4>
                    <p>По вашему запросу ничего не найдено</p>
                  </div>
              )
          }
        </Paper>
      </div>
  )
};
export default Tara;

