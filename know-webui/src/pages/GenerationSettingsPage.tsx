import React from "react";
import { Navigate } from "react-router-dom";

export const GenerationSettingsPage: React.FC = () => {
	return <Navigate to="/settings" replace />;
};
