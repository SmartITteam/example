import React from 'react';
import './App.css';
import useSnackbar from "./hooks/useSnackbar";
import SnackbarCustom from "./Components/Common/Snackbar";
import { Switch, Route } from 'react-router-dom'
import Main from "./layout/Main";
import withTheme from "./theme";
import Auth from "./layout/Auth";
import PrivateRoute from "./Components/Common/Auth/PrivateRoute";
import PublicRoute from "./Components/Common/Auth/PublicRoute";
import Register from "./pages/Main/Users/Register";
import Simple from "./layout/Simple";
import tik from "./tik.mp3";

const App = () => {
  const mySound = new Audio([tik]);
  const snackbar = useSnackbar();
  document.addEventListener(
      'notify',
      (e) => {
        snackbar.handleOpenFromEvent(e)
      },
      false
  );
  document.addEventListener(
      'soundNoty',
      (e) => {
       mySound.play();
      },
      false
  );

  return (
      <div className="App">
        <Switch>
          <PublicRoute path="/auth" exact component={Auth}/>
          <Route path="/register" exact component={Register}/>
          <PrivateRoute path="/profile" component={Simple}/>
          <PrivateRoute path="/" component={Main}/>
        </Switch>
        <SnackbarCustom {...snackbar}/>
       </div>
  );
}

export default withTheme(App);
