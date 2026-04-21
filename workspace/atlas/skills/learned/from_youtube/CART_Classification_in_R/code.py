library(rpart)
library(rpart.plot)
# Train CART model
model <- rpart(class ~ ., data=train_data, method='class', control=rpart.control(cp=0.01))
# Plot tree
rpart.plot(model, type=4, extra=101)
# Prune based on complexity parameter
opt_cp <- model$cptable[which.min(model$cptable[,'xerror']), 'CP']
pruned_model <- prune(model, cp=opt_cp)
# Predict
predictions <- predict(pruned_model, test_data, type='class')